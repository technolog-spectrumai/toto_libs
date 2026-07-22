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
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.views import View
from django.views.decorators.csrf import csrf_exempt
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

from . import presentation_format
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


def _is_presentation_file(vault_file: VaultFile) -> bool:
    try:
        return presentation_format.is_presentation(_read_raw(vault_file))
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        return False


def _get_owned_file(request, file_pk) -> VaultFile:
    """Fetch a presentation vault file owned by the user and validate its content.

    Presentations are ordinary ``.xml`` files now (``file_type="xml"``); the legacy
    ``presentation`` type is still accepted. Only files whose content is a
    ``<presentation>`` open here, so other XML can't reach the slide editor.
    """
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        owner=request.user,
        file_type__in=["xml", "presentation"],
    )
    if not _is_presentation_file(vf):
        raise Http404("Not a presentation.")
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
# Source editor (plain text)
# ---------------------------------------------------------------------------

class PresentationSourceView(LoginRequiredMixin, View):
    """Plain-text editor for the raw presentation XML (owner only).

    This is what the vault's Edit button opens — in the vault a presentation
    is just an editable XML text file. The structured slide editor stays
    reachable from the memo app (index, player, and a toolbar link here).
    """

    template_name = "memo/source.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file = _get_owned_file(request, file_pk)
        if vault_file.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vault_file)
        try:
            content = vault_file.file.read().decode("utf-8")
        except Exception:
            content = ""

        context = PageProcessor().decorate(
            {
                "vault_file": vault_file,
                "content": content,
                "save_url": reverse("memo:source_save", args=[file_pk]),
                "edit_url": reverse("memo:edit", args=[file_pk]),
                "present_url": reverse("memo:present", args=[file_pk]),
                **BaseFileDisplayView.gitvault_context(vault_file),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def presentation_source_save(request, file_pk):
    """Persist raw XML text back to the vault file.

    No validation gate — the file is plain text and the viewer already
    tolerates corrupt content. Parse state is reported so the editor can
    warn without blocking the save.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)
    content = request.POST.get("content", "")
    content_bytes = content.encode("utf-8")

    try:
        with vault_file.file.open("w") as f:
            f.write(content)
        vault_file.content_hash = hashlib.sha256(content_bytes).hexdigest()
        vault_file.file_size_bytes = len(content_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    try:
        presentation_format.loads(content)
        valid = True
    except presentation_format.PresentationParseError:
        valid = False
    return JsonResponse({"status": "ok", "valid_presentation": valid})


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
                # Hydration payload — the template emits this via {{ ...|json_script }},
                # which JSON-encodes the dict, so pass the dict (not a string).
                "presentation_json": presentation.to_dict(),
                "save_url": reverse("memo:save", args=[file_pk]),
                "present_url": reverse("memo:present", args=[file_pk]),
                # "Insert from vault" picker: the user's embeddable image/SVG files.
                "vault_media_json": _media_list(request.user),
                "embed_url": reverse("memo:media_embed"),
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


@csrf_exempt
def presentation_save(request, file_pk):
    """Persist edited slides back to the vault file as presentation XML."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    try:
        payload = json.loads(request.body or b"{}")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    presentation = presentation_format.Presentation.from_dict(payload)
    xml = presentation_format.dumps(presentation)
    xml_bytes = xml.encode("utf-8")

    try:
        with vault_file.file.open("w") as f:
            f.write(xml)
        # Hash/size from the bytes we just wrote — the FieldFile is closed once
        # the write-context exits, so we can't re-read it here.
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
        return JsonResponse({"status": "ok"})
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)


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
            file_type="xml",
            bucket=bucket,
            directory=directory,
            is_public=False,
        )
        vault_file.file.save(title, ContentFile(xml_bytes), save=False)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save()

        return redirect(reverse("memo:edit", args=[vault_file.pk]))


# ---------------------------------------------------------------------------
# Index / gallery
# ---------------------------------------------------------------------------

class PresentationIndexView(View):
    """List presentation vault files the current user can open."""

    template_name = "memo/index.html"

    # Bound the content-sniff scan (presentations share the generic xml type now).
    PRESENTATION_LIST_CAP = 300

    def get(self, request):
        from django.db.models import Q

        qs = VaultFile.objects.filter(
            file_type__in=["xml", "presentation"], is_encrypted=False
        ).select_related("owner", "bucket", "directory")
        if request.user.is_authenticated:
            qs = qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            qs = qs.filter(is_public=True)
        qs = qs.order_by("-uploaded_at", "title")[: self.PRESENTATION_LIST_CAP]

        def _location(f):
            loc = f.bucket.name if f.bucket else "—"
            if f.directory:
                loc = f"{loc} / {f.directory.full_path()}"
            return loc

        presentations = []
        for f in qs:
            try:
                raw = _read_raw(f)
            except Exception:
                continue
            if not presentation_format.is_presentation(raw):
                continue
            presentations.append({
                "title": f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": _location(f),
                "is_owner": request.user.is_authenticated and f.owner_id == request.user.id,
                "present_url": reverse("memo:present", args=[f.pk]),
                "edit_url": reverse("memo:edit", args=[f.pk]),
                "source_url": reverse("memo:source", args=[f.pk]),
            })

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "presentations": presentations,
                "buckets_json": buckets_json,
                "directories_json": directories_json,
            },
            request,
        )
        return render(request, self.template_name, context)
