"""
File-based presentation viewer + browser editor.

A presentation is a single self-contained ``.pml`` vault file
(``file_type="presentation"``) parsed by :mod:`toto.memo.presentation_format`.
These views never touch the database for presentation content — the vault file
is the single source of truth, mirroring the ``.tpy`` notebook editor in
:mod:`toto.mandragora.tpy_views`.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes

from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.files.base import ContentFile
from django.contrib import messages
from django.http import (
    Http404, HttpResponse, HttpResponseForbidden, JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View
from django.conf import settings
from django.views.decorators.http import require_POST
from django.contrib.auth.mixins import LoginRequiredMixin

from toto.editor.views import BaseFileDisplayView
from toto.ui import PageProcessor
from toto.vault.filetree import accessible_files
from toto.vault.models import VaultFile
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)

from . import bundle, presentation_format, render_pdf, tiptap
from .media import clean_svg_markup, image_bytes_to_data_uri

# Vault file types that can be embedded into a slide body.
_MEDIA_TYPES = ["image", "svg"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_raw(vault_file: VaultFile) -> str:
    """Raw UTF-8 text of the vault file via a fresh storage handle."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


def _read_head(vault_file: VaultFile, size: int = 2048) -> bytes:
    """The first bytes of a file, for the identity sniff.

    A fresh storage handle, so it never disturbs the `FieldFile` cursor, and a
    bounded read — the gallery does this for every candidate file on the page,
    and a full read there means pulling every deck's embedded images off disk
    just to look at one tag.
    """
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read(size)


def _adopt(vault_file: VaultFile) -> None:
    """Retype a legacy deck that is still filed as generic XML.

    Decks were briefly stored as ``file_type="xml"`` and identified purely by
    sniffing their content. That left them with no Play button at all and an
    Edit button that opened the generic XML editor — because a vault plugin only
    fires when its `key` equals a file_type, and `xml` belongs to `toto.editor`.
    Rather than migrate every host's vault, memo repairs a row the first time it
    touches one: the fix arrives with the deck being opened, and costs nothing
    for anyone who has none.
    """
    if vault_file.file_type != "presentation":
        VaultFile.objects.filter(pk=vault_file.pk).update(file_type="presentation")
        vault_file.file_type = "presentation"


def _is_presentation_file(vault_file: VaultFile) -> bool:
    try:
        return presentation_format.is_presentation(_read_raw(vault_file))
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        return False


def _get_owned_file(request, file_pk) -> VaultFile:
    """Fetch a presentation vault file owned by the user and validate its content.

    Both types are accepted on the way in: ``presentation`` is what memo writes
    today, and ``xml`` is what the brief content-sniffing era left behind. Only
    files whose content really is a ``<presentation>`` open here, so other XML
    cannot reach the slide editor.
    """
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        owner=request.user,
        file_type__in=["xml", "presentation"],
    )
    if not _is_presentation_file(vf):
        raise Http404("Not a presentation.")
    _adopt(vf)
    return vf


def _read_presentation(vault_file: VaultFile) -> presentation_format.Presentation:
    try:
        raw = vault_file.file.read().decode("utf-8")
    except Exception:
        raw = ""
    try:
        return presentation_format.loads(raw)
    except presentation_format.PresentationParseError:
        # Corrupt / non-presentation content — start blank rather than blowing
        # up the editor.  Saving overwrites with valid XML.
        return presentation_format.new_presentation(title=vault_file.title)


def _media_list(user) -> list[dict]:
    """Flat list of the user's embeddable image/SVG vault files for the picker,
    each with a human-readable ``location`` (``<bucket> / <folder path>``).
    Access-checked via :func:`accessible_files`; encrypted files are skipped
    (their bytes are ciphertext and can't be embedded)."""
    files = (
        accessible_files(user, file_types=_MEDIA_TYPES)
        .filter(is_encrypted=False)
        .order_by("bucket__name", "title")[:500]
    )
    items = []
    for f in files:
        bname = f.bucket.name if f.bucket else "—"
        location = f"{bname} / {f.directory.full_path()}" if f.directory_id else bname
        items.append(
            {
                "pk": f.id,
                "title": f.title or f.key,
                "file_type": f.file_type,
                "location": location,
            }
        )
    return items


# ---------------------------------------------------------------------------
# Viewer
# ---------------------------------------------------------------------------

class PresentationView(View):
    """Render a presentation vault file as a reveal.js slideshow."""

    template_name = "memo/present.html"

    def get(self, request, file_pk):
        vault_file = get_object_or_404(
            VaultFile.objects.select_related("bucket", "directory", "owner"),
            pk=file_pk,
            file_type__in=["xml", "presentation"],
        )

        if vault_file.is_encrypted:
            return HttpResponseForbidden("Cannot display an encrypted file.")
        if not _is_presentation_file(vault_file):
            raise Http404("Not a presentation.")
        _adopt(vault_file)

        # Visibility check mirrors toto.vod.views.vault_file_play.
        if not vault_file.is_public:
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            can_access = vault_file.owner == request.user
            if not can_access and vault_file.directory:
                can_access = vault_file.directory.user_can_access(request.user)
            if not can_access:
                return HttpResponseForbidden()

        presentation = _read_presentation(vault_file)

        # Owner gets an Edit link back into the browser editor.
        can_edit = request.user.is_authenticated and vault_file.owner == request.user

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "presentation": presentation,
                "edit_url": reverse("memo:edit", args=[vault_file.pk]) if can_edit else "",
            },
            request,
        )
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

class PresentationEditView(LoginRequiredMixin, View):
    """In-browser editor for a presentation vault file (owner only)."""

    template_name = "memo/edit.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        if vault_file.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vault_file)
        presentation = _read_presentation(vault_file)

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                # Hydration payloads — the template emits these via
                # {{ ...|json_script }}, which JSON-encodes what it is given, so
                # these must be plain Python. Handing it an already-serialised
                # string produces an island that parses back into a *string* and
                # every property on it is undefined.
                "presentation_json": presentation.to_dict(),
                "vault_media_json": _media_list(request.user),
                "config_json": {
                    "canEdit": True,
                    # The editor sends this back with every save; the endpoint
                    # answers 409 if the file moved on underneath it.
                    "contentHash": vault_file.content_hash or "",
                    "urls": {
                        "save": reverse("memo:save", args=[file_pk]),
                        "embed": reverse("memo:media_embed"),
                        "upload": reverse("memo:media_upload"),
                    },
                    # Strings the editor puts in a browser prompt/dialog, where
                    # a {% trans %} in the template cannot reach.
                    "text": {
                        "linkPrompt": _("Link address"),
                    },
                },
                # The import map for the prose field's ES module — built
                # server-side so every URL is hashed static. See tiptap.py.
                "tiptap_import_map": tiptap.import_map_json(),
                "present_url": reverse("memo:present", args=[file_pk]),
                "export_pdf_url": reverse("memo:export_pdf", args=[file_pk]),
                "export_zip_url": reverse("memo:export_zip", args=[file_pk]),
                # The git toolbar moved here from the retired source page — a
                # deck under gitvault keeps its history buttons.
                **BaseFileDisplayView.gitvault_context(vault_file),
            },
            request,
        )
        return render(request, self.template_name, context)


@login_required
def presentation_media_embed(request):
    """Return an embeddable snippet payload for a vault image/SVG the user can read.

    ``GET ?file_pk=<pk>`` → ``{"kind": "svg", "markup": …, "alt": …}`` for SVGs, or
    ``{"kind": "image", "data_uri": …, "alt": …}`` for rasters. Access is gated to
    files the user may read (:func:`accessible_files`); the caller inlines the
    result into the slide body, keeping the ``.pml`` self-contained.
    """
    try:
        file_pk = int(request.GET.get("file_pk", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "file_pk is required."}, status=400)

    vf = get_object_or_404(
        accessible_files(request.user, file_types=_MEDIA_TYPES).filter(is_encrypted=False),
        pk=file_pk,
    )

    try:
        with vf.file.open("rb") as fh:
            raw = fh.read()
    except Exception as exc:
        return JsonResponse({"error": f"Could not read file: {exc}"}, status=500)

    alt = (vf.title or vf.key or "image").rsplit(".", 1)[0]
    if vf.file_type == "svg":
        markup = clean_svg_markup(raw.decode("utf-8", errors="replace"))
        return JsonResponse({"kind": "svg", "markup": markup, "alt": alt})

    mime, _ = mimetypes.guess_type(vf.title or vf.key or "")
    data_uri = image_bytes_to_data_uri(raw, mime or "")
    return JsonResponse({"kind": "image", "data_uri": data_uri, "alt": alt})


@login_required
@require_POST
def presentation_media_upload(request):
    """Embed a file dropped onto a slide, or picked with the file input.

    The bytes go through the server rather than a canvas in the browser, and
    that is deliberate on two counts. The resize policy stays in one place —
    `media.image_bytes_to_data_uri`, which the vault picker already uses — so
    the two paths cannot drift. And an SVG gets sanitised by code that cannot be
    skipped by posting here directly.
    """
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "No file."}, status=400)
    if upload.size > getattr(settings, "MEMO_MAX_UPLOAD_BYTES", 20 * 1024 * 1024):
        return JsonResponse({"error": "That file is too large to embed."}, status=413)

    raw = upload.read()
    name = upload.name or "image"
    alt = name.rsplit(".", 1)[0]

    mime, _ = mimetypes.guess_type(name)
    if (mime or "") == "image/svg+xml" or name.lower().endswith(".svg"):
        return JsonResponse({
            "kind": "svg",
            "payload": clean_svg_markup(raw.decode("utf-8", errors="replace")),
            "alt": alt,
        })
    if not (mime or "").startswith("image/"):
        return JsonResponse({"error": "Only images and SVGs can be embedded."},
                            status=400)
    return JsonResponse({
        "kind": "image",
        "payload": image_bytes_to_data_uri(raw, mime or ""),
        "alt": alt,
    })


def _read_json_body(request, limit: int):
    """The request body, without Django's 2.5 MB form ceiling.

    `request.body` is checked against DATA_UPLOAD_MAX_MEMORY_SIZE, which no host
    sets and therefore defaults to 2.5 MB. A deck with about twenty embedded
    640px images is already past that, so saving one raised RequestDataTooBig
    before this view ever ran — and autosave would turn a rare failure into a
    constant one. `request.read()` is not size-checked, so the limit becomes
    ours to state, and it is stated here.

    Safe to call after the CSRF middleware: for `application/json` Django's
    `request.POST` never touches the stream, so it is still unread.
    """
    raw = request.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("too-big")
    return json.loads(raw or b"{}")


@require_POST
def presentation_save(request, file_pk):
    """Persist edited slides back to the vault file as presentation XML."""
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    limit = getattr(settings, "MEMO_MAX_DECK_BYTES", 32 * 1024 * 1024)
    try:
        payload = _read_json_body(request, limit)
    except ValueError as exc:
        if str(exc) == "too-big":
            return JsonResponse(
                {"error": f"That deck is larger than {limit // (1024 * 1024)} MB. "
                          "Remove or shrink an image and try again."}, status=413)
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    # Optimistic concurrency. Autosave fires on a timer, and the same deck can
    # be open in two tabs — without this the slower one silently wins and the
    # other's work is gone with no error anywhere.
    base_hash = payload.get("base_hash")
    if base_hash and vault_file.content_hash and base_hash != vault_file.content_hash:
        return JsonResponse(
            {"error": "This deck changed somewhere else since you opened it.",
             "content_hash": vault_file.content_hash}, status=409)

    document = payload.get("presentation") or payload
    presentation = presentation_format.Presentation.from_dict(document)
    xml = presentation_format.dumps(presentation)
    xml_bytes = xml.encode("utf-8")

    try:
        with vault_file.file.open("w") as f:
            f.write(xml)
        # Hash and size from the bytes just written — the FieldFile is closed
        # once the write-context exits, so it cannot be re-read here.
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception as exc:                       # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=500)

    return JsonResponse({"status": "ok", "content_hash": vault_file.content_hash})


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class PresentationCreateView(LoginRequiredMixin, View):
    """Create a blank presentation in a chosen bucket/directory (or the user's
    personal bucket root when none is picked) and drop straight into the editor."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        bucket, directory = resolve_new_file_target(
            request.user,
            request.POST.get("bucket_id"),
            request.POST.get("directory_id"),
        )

        raw = (request.POST.get("filename") or "").strip()
        base = raw[:-4] if raw.lower().endswith(".xml") else raw
        base = base.strip() or "untitled-presentation"
        title = f"{base}.xml"
        key = _unique_file_key(slugify(base), bucket)

        xml = presentation_format.dumps(
            presentation_format.new_presentation(base)
        )
        xml_bytes = xml.encode("utf-8")

        vault_file = VaultFile(
            owner=request.user,
            title=title,
            key=key,
            file_type="presentation",
            bucket=bucket,
            directory=directory,
            is_public=False,
        )
        vault_file.file.save(title, ContentFile(xml_bytes), save=False)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save()

        return redirect(reverse("memo:edit", args=[vault_file.pk]))


class PresentationImportView(LoginRequiredMixin, View):
    """Restore a deck from an exported ZIP.

    Always creates a NEW file, never overwrites one. A restore that can destroy
    the deck you were trying to protect is the wrong shape for a backup.
    """

    login_url = reverse_lazy("core:login")

    def post(self, request):
        upload = request.FILES.get("archive")
        if upload is None:
            messages.error(request, "Choose a .zip export to import.")
            return redirect("memo:index")
        if upload.size > getattr(settings, "MEMO_MAX_DECK_BYTES", 32 * 1024 * 1024):
            messages.error(request, "That archive is too large to import.")
            return redirect("memo:index")

        try:
            presentation = bundle.import_zip(upload.read())
        except bundle.BundleError as exc:
            messages.error(request, str(exc))
            return redirect("memo:index")

        bucket, directory = resolve_new_file_target(
            request.user,
            request.POST.get("bucket_id"),
            request.POST.get("directory_id"),
        )
        base = (presentation.title
                or (upload.name or "imported").rsplit(".", 1)[0]) or "imported"
        title = f"{base}.xml"
        xml_bytes = presentation_format.dumps(presentation).encode("utf-8")

        vault_file = VaultFile(
            owner=request.user, title=title,
            key=_unique_file_key(slugify(base) or "imported", bucket),
            file_type="presentation", bucket=bucket, directory=directory,
            is_public=False,
        )
        vault_file.file.save(title, ContentFile(xml_bytes), save=False)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save()

        messages.success(request, f"Imported {title}.")
        return redirect(reverse("memo:edit", args=[vault_file.pk]))


@login_required
def presentation_export_zip(request, file_pk):
    """The deck as a readable archive: XML plus its pictures as real files."""
    vault_file = _get_owned_file(request, file_pk)
    presentation = _read_presentation(vault_file)
    raw = bundle.export_zip(presentation)

    base = (vault_file.title or "presentation").rsplit(".", 1)[0]
    response = HttpResponse(raw, content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="{slugify(base) or "deck"}.zip"'
    return response


@login_required
def presentation_export_pdf(request, file_pk):
    """One page per slide, rendered from the same slide.css as everything else."""
    vault_file = _get_owned_file(request, file_pk)
    presentation = _read_presentation(vault_file)
    try:
        raw = render_pdf.render(presentation)
    except render_pdf.PdfUnavailable as exc:
        # A deployment fact, not something the user can fix by trying again.
        return HttpResponse(str(exc), status=503, content_type="text/plain")

    base = (vault_file.title or "presentation").rsplit(".", 1)[0]
    response = HttpResponse(raw, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{slugify(base) or "deck"}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Index / gallery
# ---------------------------------------------------------------------------

class PresentationIndexView(View):
    """The gallery: every deck the current user can open, with a thumbnail."""

    template_name = "memo/index.html"
    PER_PAGE = 12

    # Only legacy rows still need sniffing, and only until they are opened
    # once — a typed deck is found by the database. This bounds the tail.
    SNIFF_CAP = 300

    def get(self, request):
        from django.core.paginator import Paginator
        from django.db.models import Q

        qs = VaultFile.objects.filter(
            file_type__in=["xml", "presentation"], is_encrypted=False
        ).select_related("owner", "bucket", "directory")
        if request.user.is_authenticated:
            qs = qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            qs = qs.filter(is_public=True)
        qs = qs.order_by("-uploaded_at", "title")

        rows, stale, sniffed = [], [], 0
        for f in qs:
            if f.file_type != "presentation":
                # A legacy row. Read the first 2 KB, not the whole file: a full
                # parse here means pulling every deck's embedded images off disk
                # just to look at one tag.
                if sniffed >= self.SNIFF_CAP:
                    continue
                sniffed += 1
                try:
                    head = _read_head(f)
                except Exception:                      # noqa: BLE001
                    continue
                if not presentation_format.sniff_is_presentation(head):
                    continue
                stale.append(f.pk)
            rows.append(f)

        # One query, not one per row — see _adopt.
        if stale:
            VaultFile.objects.filter(pk__in=stale).update(file_type="presentation")

        page = Paginator(rows, self.PER_PAGE).get_page(request.GET.get("page"))

        presentations = []
        for f in page.object_list:
            presentations.append({
                "title": f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": _location_of(f),
                "is_owner": request.user.is_authenticated and f.owner_id == request.user.id,
                "present_url": reverse("memo:present", args=[f.pk]),
                "edit_url": reverse("memo:edit", args=[f.pk]),
                # Only the current page is parsed — which is the point of
                # paginating at all. A gallery of 300 decks used to read and
                # fully parse all 300 files on every visit.
                **_cover(f),
            })

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "presentations": presentations,
                "page_obj": page,
                "is_paginated": page.has_other_pages(),
                "buckets_json": buckets_json,
                "directories_json": directories_json,
            },
            request,
        )
        return render(request, self.template_name, context)


def _location_of(vault_file) -> str:
    location = vault_file.bucket.name if vault_file.bucket else "\u2014"
    if vault_file.directory:
        location = f"{location} / {vault_file.directory.full_path()}"
    return location


def _cover(vault_file) -> dict:
    """Slide one and the deck theme, for the card thumbnail.

    The thumbnail is a real slide rendered through the same `_slide.html` and
    `slide.css` as the player, just scaled down — so a slide that is too full
    looks too full on the card. A screenshot or a text summary would be one more
    thing to keep in step.
    """
    try:
        presentation = presentation_format.loads(_read_raw(vault_file))
    except Exception:                                  # noqa: BLE001
        return {"cover": None, "theme": "black", "font": "sans", "slide_count": 0}
    return {
        "cover": presentation.slides[0] if presentation.slides else None,
        "theme": presentation.theme,
        "font": presentation.font,
        "slide_count": len(presentation.slides),
    }
