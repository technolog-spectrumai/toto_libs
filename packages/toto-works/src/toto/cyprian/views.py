"""The library, the writer, the reader, and the small endpoints behind them.

Every page render goes through `PageProcessor` or the base template comes out
unstyled. A document is one vault file, so there is no model layer here: the
file is read, parsed, rendered, and written back.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes

from django.apps import apps
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.files.base import ContentFile
from django.http import (
    FileResponse, Http404, HttpResponse, HttpResponseForbidden,
    HttpResponseNotAllowed, JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_POST

from toto.editor.views import BaseFileDisplayView
from toto.memo.media import clean_svg_markup, image_bytes_to_data_uri
from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault import access, locks, versions
from toto.vault.filetree import accessible_files
from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
from toto.cyprian.models import CyprianQuotaPolicy, CyprianUsageEvent
from toto.vault.models import VaultFile
from toto.vault.views import new_file_picker_json, resolve_new_file_target

from toto.memo import tiptap

from . import clean_html, document_format, render_pdf
from .bridge import DocumentBridge, open_document
from .bridge import may_edit as bridge_may_edit
from .bridge import write_back as _bridge_write_back
from .sanitize_html import sanitize_content
from .surface import document_editor_shown

# Vault file types that can be embedded into a document.
_MEDIA_TYPES = ["image", "svg"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_raw(vault_file: VaultFile) -> str:
    """Raw UTF-8 text via a fresh storage handle."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


def _read_head(vault_file: VaultFile, size: int = 2048) -> bytes:
    """The first bytes, for the identity sniff — bounded, for listings."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read(size)


def _is_document_file(vault_file: VaultFile) -> bool:
    try:
        return document_format.is_document(_read_raw(vault_file))
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        return False


def _adopt(vault_file: VaultFile) -> None:
    """Retype a document that is still filed as generic XML.

    Someone can upload a document's `.xml` by hand, and `_EXT_MAP` types it
    `xml`. Repairing the row the first time cyprian touches it means the vault
    buttons start working without a migration over everybody's files.
    """
    if vault_file.file_type != "document":
        VaultFile.objects.filter(pk=vault_file.pk).update(file_type="document")
        vault_file.file_type = "document"


def _get_owned_file(request, file_pk) -> VaultFile:
    """A DOCUMENT of this user's. Strict ownership, in the query.

    Still the gate for everything that reads a document out whole or writes a
    file beside it — `document_source` (raw XML, no renderer, no sanitiser),
    `document_export_pdf`, `document_save_pdf`, `document_save_html`. Those are
    deliberately NOT bridged: two of them write into `vault_file.bucket`, and
    `_save_beside` overwrites a same-named file there without an owner filter,
    which is safe only while the requester is guaranteed to be the owner.

    `_open_document` below is the bridged gate, and it covers the two endpoints
    a shared document actually needs: opening the writer, and saving it.
    """
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk, owner=request.user, file_type__in=["document", "xml"])
    if not _is_document_file(vault_file):
        raise Http404("Not a document.")
    _adopt(vault_file)
    return vault_file


def _open_document(request, file_pk):
    """The file and its parsed document, if this user may EDIT it.

    The owner filter moves out of the QUERY and into a decision made afterwards,
    because ownership is no longer the only right answer: a project wiki page is
    written by the project's team, none of whom hold the bytes. So the row is
    fetched unfiltered, the owning app is asked, and a stranger is refused.

    The order is forced by where the answer lives — the bridge is resolved from
    the FILE, but a bridge may want the parsed document too, and the document is
    inside the file. Read once, parse once, hand both back. That is cheaper than
    what it replaces: `_get_owned_file` + `_read_document` read and parsed the
    same bytes TWICE on every writer open.

    404, not 403, on refusal. It is what a stranger has always got here, and it
    keeps a page's existence quiet — the same choice kanban makes for missions
    they cannot see.

    Returns `(vault_file, document)`. The document is None only for an encrypted
    file, which is returned unread so each caller's own `is_encrypted` branch can
    say its own sentence.
    """
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk, file_type__in=["document", "xml"])

    if vault_file.is_encrypted:
        # Nothing to parse and nothing to authorise against: an encrypted
        # document has no readable meta and no bridge can claim it, so only its
        # owner can be here.
        if vault_file.owner_id != request.user.pk:
            raise Http404("Not a document.")
        return vault_file, None

    try:
        raw = _read_raw(vault_file)
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        raise Http404("Not a document.")
    if not document_format.is_document(raw):
        raise Http404("Not a document.")

    try:
        document = document_format.loads(raw)
    except document_format.DocumentParseError:
        # Corrupt content — start from a blank rather than blowing up the
        # writer. Saving overwrites with valid XML.
        document = document_format.new_document(title=vault_file.title)

    if vault_file.owner_id == request.user.pk:
        _adopt(vault_file)
        return vault_file, document

    # Deliberately AFTER the ownership branch: _adopt writes to the row, and a
    # non-owner should not be able to retype somebody's file by looking at it.
    # A bridged document was minted as file_type="document" anyway.
    #
    # The decision itself lives in bridge.may_edit, which the vault's lock and
    # version endpoints also ask (through VaultAccessPlugin) — one answer, so
    # "may open the writer" and "may hold the lock" cannot drift apart.
    if bridge_may_edit(request.user, vault_file, document):
        return vault_file, document

    raise Http404("Not a document.")


def _owned_file(request, file_pk, *, types=None) -> VaultFile:
    """Any vault file of this user's — a contract, a rendition, anything.

    `_get_owned_file` above is for DOCUMENTS: it filters on the document types
    and adopts a stray `.xml`. Everything else this app touches — the
    `.contract` a body was opened from, the PDF a save produced — needs the
    ownership check without the document assumption.
    """
    query = VaultFile.objects.select_related("bucket", "directory", "owner")
    if types:
        query = query.filter(file_type__in=types)
    return get_object_or_404(query, pk=file_pk, owner=request.user)


def _read_document(vault_file: VaultFile) -> document_format.Document:
    try:
        return document_format.loads(_read_raw(vault_file))
    except document_format.DocumentParseError:
        # Corrupt content — start from a blank rather than blowing up the
        # writer. Saving overwrites with valid XML.
        return document_format.new_document(title=vault_file.title)


def _unique_key(base: str, bucket) -> str:
    key, n = base or "document", 1
    while VaultFile.objects.filter(bucket=bucket, key=key).exists():
        n += 1
        key = f"{base}-{n}"
    return key


def _media_list(user) -> list[dict]:
    rows = (accessible_files(user, file_types=_MEDIA_TYPES)
            .filter(is_encrypted=False)
            .select_related("bucket", "directory")
            .order_by("bucket__name", "title")[:500])
    out = []
    for f in rows:
        location = f.bucket.name if f.bucket else "—"
        if f.directory:
            location = f"{location} / {f.directory.full_path()}"
        out.append({"pk": f.pk, "title": f.title,
                    "file_type": f.file_type, "location": location})
    return out


def _picker_data(user):
    """Buckets and directories the user may save into, as plain Python.

    The same data `new_file_picker_json` serves the New Document flow, but
    unserialised — `json_script` does the serialising here, and handing it a
    pre-serialised string produces an island that parses back into a *string*.
    """
    import json as _json
    buckets_json, dirs_json = new_file_picker_json(user)
    return {"buckets": _json.loads(buckets_json),
            "directories": _json.loads(dirs_json)}


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------

class DocumentIndexView(View):
    """Every document the current user can open."""

    template_name = "cyprian/index.html"
    PER_PAGE = 12
    SNIFF_CAP = 300

    def get(self, request):
        from django.core.paginator import Paginator
        from django.db.models import Q

        # The library is the browsable half of this app, so it is what
        # SHOW_DOCUMENT_EDITOR turns off — 404, the same answer a host that
        # never installed cyprian gives, rather than an empty page that implies
        # the feature is here and you have nothing. The writer itself stays
        # mounted either way: kanban's wiki pages open at cyprian:edit and must
        # keep opening.
        if not document_editor_shown():
            raise Http404("The document library is not enabled on this host.")

        qs = VaultFile.objects.filter(
            file_type__in=["document", "xml"], is_encrypted=False
        ).filter(access.local_content_q()
        ).select_related("owner", "bucket", "directory")
        if request.user.is_authenticated:
            qs = qs.filter(Q(is_public=True) | Q(owner=request.user))
        else:
            qs = qs.filter(is_public=True)
        qs = qs.order_by("-uploaded_at", "title")

        rows, stale, sniffed = [], [], 0
        for f in qs:
            if f.file_type != "document":
                if sniffed >= self.SNIFF_CAP:
                    continue
                sniffed += 1
                try:
                    head = _read_head(f)
                except Exception:                      # noqa: BLE001
                    continue
                if not document_format.sniff_is_document(head):
                    continue
                stale.append(f.pk)
            rows.append(f)

        if stale:
            VaultFile.objects.filter(pk__in=stale).update(file_type="document")

        page = Paginator(rows, self.PER_PAGE).get_page(request.GET.get("page"))

        documents = []
        for f in page.object_list:
            location = f.bucket.name if f.bucket else "—"
            if f.directory:
                location = f"{location} / {f.directory.full_path()}"
            # Only the current page is parsed — which is the point of paginating.
            try:
                doc = document_format.loads(_read_raw(f))
            except Exception:                          # noqa: BLE001
                continue
            documents.append({
                "file_pk": f.pk,
                "title": doc.title or f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": location,
                "is_owner": request.user.is_authenticated and f.owner_id == request.user.id,
                "read_url": reverse("cyprian:read", args=[f.pk]),
                "edit_url": reverse("cyprian:edit", args=[f.pk]),
                "words": doc.word_count,
                "minutes": doc.reading_time_minutes,
                "headings": len(doc.outline),
                "document": doc,
            })

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate({
            "documents": documents,
            "page_obj": page,
            "is_paginated": page.has_other_pages(),
            "buckets_json": buckets_json,
            "directories_json": directories_json,
        }, request)
        return render(request, self.template_name, context)


class DocumentCreateView(LoginRequiredMixin, View):
    """Create a blank document and drop straight into the writer."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        # Goes with the library: a standalone document is the thing this app
        # offers on its own account, and the only button that reaches here lives
        # on the index. A wiki page is minted by its owning app's bridge, not
        # through this view, so nothing the boards need runs through this gate.
        if not document_editor_shown():
            raise Http404("The document library is not enabled on this host.")

        bucket, directory = resolve_new_file_target(
            request.user, request.POST.get("bucket_id"),
            request.POST.get("directory_id"))

        raw = (request.POST.get("filename") or "").strip()
        base = raw[:-4] if raw.lower().endswith(".xml") else raw
        base = base.strip() or "untitled-document"
        title = f"{base}.xml"

        xml = document_format.dumps(document_format.new_document(base))
        xml_bytes = xml.encode("utf-8")

        vault_file = VaultFile(
            owner=request.user, title=title,
            key=_unique_key(slugify(base) or "document", bucket),
            file_type="document", bucket=bucket, directory=directory,
            is_public=False)
        vault_file.file.save(title, ContentFile(xml_bytes), save=False)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save()

        return redirect(reverse("cyprian:edit", args=[vault_file.pk]))


# ---------------------------------------------------------------------------
# Reader
# ---------------------------------------------------------------------------

class DocumentReadView(View):
    """The document, rendered for reading and sharing."""

    template_name = "cyprian/read.html"

    def get(self, request, file_pk):
        vault_file = get_object_or_404(
            VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
            pk=file_pk, file_type__in=["document", "xml"])

        if vault_file.is_encrypted:
            return HttpResponseForbidden("Cannot display an encrypted file.")

        # Read once, parse once. The sniff, the visibility ladder and the render
        # all want the same bytes, and this page used to fetch them from storage
        # three times over to answer three questions about one file.
        try:
            raw = _read_raw(vault_file)
        except (FileNotFoundError, UnicodeDecodeError, ValueError):
            raise Http404("Not a document.")
        if not document_format.is_document(raw):
            raise Http404("Not a document.")
        _adopt(vault_file)

        try:
            document = document_format.loads(raw)
        except document_format.DocumentParseError:
            document = document_format.new_document(title=vault_file.title)

        # The owning app, if this document is really a view onto one of its
        # objects — a wiki page, a contract's prose.
        match = DocumentBridge.for_file(vault_file, document)

        # Visibility ladder, mirroring toto.memo's player.
        if not vault_file.is_public:
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            allowed = vault_file.owner == request.user
            # The owning app's readers come BEFORE the vault's folder ACL: a
            # wiki page's readers are decided by its project, and none of them
            # are in the file owner's directory whitelist.
            if not allowed and match is not None:
                allowed = match[0].can_read(request.user, match[1])
            if not allowed and vault_file.directory:
                allowed = vault_file.directory.user_can_access(request.user)
            if not allowed:
                return HttpResponseForbidden()

        can_edit = request.user.is_authenticated and (
            vault_file.owner == request.user
            or (match is not None and match[0].can_edit(request.user, match[1])))

        context = PageProcessor().decorate({
            "vault_file": vault_file,
            "document": document,
            "edit_url": reverse("cyprian:edit", args=[file_pk]) if can_edit else "",
            "pdf_url": reverse("cyprian:export_pdf", args=[file_pk]) if can_edit else "",
        }, request)
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------

class DocumentEditView(LoginRequiredMixin, View):
    template_name = "cyprian/edit.html"
    login_url = reverse_lazy("core:login")

    def get(self, request, file_pk):
        vault_file, document = _open_document(request, file_pk)
        if vault_file.is_encrypted:
            from toto.vault.access import encrypted_lock_response
            return encrypted_lock_response(request, vault_file)

        # Who is at the keyboard decides three things the template needs: whether
        # Delete is offered at all (the vault's endpoint is owner-only, so a
        # bridged editor's click would 404 into silence), whether renditions can
        # be written beside the file, and where the back arrow goes.
        is_owner = vault_file.owner_id == request.user.pk
        match = DocumentBridge.for_file(vault_file, document)

        context = PageProcessor().decorate({
            "vault_file": vault_file,
            "can_delete": is_owner,
            "return_url": match[0].return_url(match[1]) if match else "",
            "return_label": match[0].return_label(match[1]) if match else "",
            # Plain Python, not JSON strings — `json_script` serialises what it
            # is given, and handing it something already serialised produces an
            # island that parses back into a *string* with every property
            # undefined.
            "document_json": document.to_dict(),
            "vault_media_json": _media_list(request.user),
            # The export modal's destination picker — the same bucket/folder
            # "save-as" data the New Document flow uses, straight from vault.
            "picker_json": _picker_data(request.user),
            "config_json": {
                "canEdit": True,
                "filePk": vault_file.pk,
                "contentHash": vault_file.content_hash or "",
                "urls": {
                    "save": reverse("cyprian:save", args=[file_pk]),
                    "source": reverse("cyprian:source", args=[file_pk]),
                    "savePdf": reverse("cyprian:save_pdf", args=[file_pk]),
                    "saveHtml": reverse("cyprian:save_html", args=[file_pk]),
                    "embed": reverse("cyprian:media_embed"),
                    "upload": reverse("cyprian:media_upload"),
                    # The vault's own delete — reused, not duplicated.
                    "destroy": reverse("vault:delete_file"),
                    "index": reverse("cyprian:index"),
                },
                # The suggestion the save prompt starts from: the file's own
                # name, which is the only name the writer has ever given this
                # document — there is no title field in the editor.
                "renditionBase": slugify(
                    (vault_file.title or "document").rsplit(".", 1)[0]) or "document",
                # Where a rendition goes unless the writer picks otherwise:
                # beside the document, or — for someone editing a document they
                # do not own — nowhere in particular, so the picker asks. Must
                # agree with _asked_target, which refuses to file a non-owner's
                # export into the owner's bucket.
                "home": ({"bucket": vault_file.bucket_id,
                          "directory": vault_file.directory_id or 0}
                         if is_owner else {"bucket": 0, "directory": 0}),
                "text": {
                    "namePrompt": _("File name"),
                },
            },
            # The import map for the vendored TipTap modules. Built in Python
            # so every module goes through static() and is cache-busted with
            # the rest of the site — see memo/tiptap.py, which owns the vendored
            # files because both editors in this wheel run on TipTap.
            "tiptap_import_map": tiptap.import_map_json(),
            "read_url": reverse("cyprian:read", args=[file_pk]),
            "pdf_url": reverse("cyprian:export_pdf", args=[file_pk]),
            # "" on a host without the assistant, or for a file whose
            # bucket carries the AI shield — the template renders nothing at
            # all. See toto.core.assistant, which is why cyprian never names
            # toto-ai.
            "steven_surface": assistant.surface_for_file("cyprian", vault_file),
            **BaseFileDisplayView.repo_context(vault_file, request.user),
        }, request)
        return render(request, self.template_name, context)


def _read_json_body(request, limit: int):
    """The request body, without Django's 2.5 MB form ceiling.

    `request.body` is checked against DATA_UPLOAD_MAX_MEMORY_SIZE, which no host
    sets and therefore defaults to 2.5 MB — a document with a handful of embedded
    images is past that, and autosave would turn a rare failure into a constant
    one. `request.read()` is not size-checked, so the limit becomes ours to
    state, and it is stated here.
    """
    raw = request.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("too-big")
    return json.loads(raw or b"{}")


@require_POST
def document_save(request, file_pk):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file, _opened = _open_document(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."},
                            status=403)

    limit = getattr(settings, "CYPRIAN_MAX_DOCUMENT_BYTES", 32 * 1024 * 1024)
    try:
        payload = _read_json_body(request, limit)
    except ValueError as exc:
        if str(exc) == "too-big":
            return JsonResponse(
                {"error": f"That document is larger than {limit // (1024 * 1024)} MB. "
                          "Remove or shrink an image and try again."}, status=413)
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)

    # The editing lock is the first line: someone else holding it means this
    # save should never have been attempted. 423, not 409 — a retry cannot
    # succeed until they leave, so inviting one would be a lie.
    if not locks.may_write(vault_file, request.user):
        held = locks.holder_of(vault_file)
        return JsonResponse(
            {"error": f"{held.holder} is editing this document.",
             "locked_by": held.holder.get_username()}, status=423)

    # Optimistic concurrency: autosave fires on a timer and the same document
    # can be open twice. Without this the slower writer silently wins.
    base_hash = payload.get("base_hash")
    if base_hash and vault_file.content_hash and base_hash != vault_file.content_hash:
        # Refuse the write, but KEEP the work. Refusing alone is what the user
        # experiences as "it lost my paragraph" — and these documents cannot be
        # merged (the whole body is one CDATA line), so the honest answer is
        # two versions and a human, which is what every other document product
        # settled on too.
        rescued = None
        try:
            document = document_format.Document.from_dict(
                payload.get("document") or payload)
            rescued = versions.save_conflicting_draft(
                vault_file,
                body=document_format.dumps(document).encode("utf-8"),
                author=request.user)
        except Exception:                              # noqa: BLE001
            pass                                       # never turn a 409 into a 500
        return JsonResponse(
            {"error": "This document changed somewhere else since you opened it. "
                      "Your text was kept as a version so nothing is lost.",
             "content_hash": vault_file.content_hash,
             "kept_as_version": rescued.number if rescued else None}, status=409)

    document = document_format.Document.from_dict(
        payload.get("document") or payload)
    xml = document_format.dumps(document)
    xml_bytes = xml.encode("utf-8")

    try:
        with vault_file.file.open("w") as fh:
            fh.write(xml)
        vault_file.content_hash = hashlib.sha256(xml_bytes).hexdigest()
        vault_file.file_size_bytes = len(xml_bytes)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception as exc:                           # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=500)

    # A document that belongs to something else writes its body back, so the
    # owning app's own surfaces render what was just written — notarius's
    # Generate PDF, a kanban wiki page. Resolved from the FILE, not from the
    # payload's meta: this very request could have rewritten that.
    _bridge_write_back(vault_file, document, user=request.user)

    return JsonResponse({"status": "ok", "content_hash": vault_file.content_hash})


@login_required
def document_source(request, file_pk):
    """The document as the file holds it, and back again.

    GET returns the XML as text/plain. POST takes XML, parses it with the SAME
    parser every other path uses, and returns the parsed document as a dict for
    the editor to load — without saving anything. Applying it is then an
    ordinary edit that goes through undo and autosave like any other, so a bad
    paste can be undone rather than being already on disk.

    One parser, deliberately. A second one written in JavaScript would drift,
    and the two would disagree about a malformed file at the worst moment.
    """
    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."},
                            status=403)

    if request.method == "GET":
        return HttpResponse(_read_raw(vault_file),
                            content_type="text/plain; charset=utf-8")
    if request.method != "POST":
        return HttpResponseNotAllowed(["GET", "POST"])

    limit = getattr(settings, "CYPRIAN_MAX_DOCUMENT_BYTES", 32 * 1024 * 1024)
    raw = request.read(limit + 1)
    if len(raw) > limit:
        return JsonResponse(
            {"error": f"That is larger than {limit // (1024 * 1024)} MB."},
            status=413)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return JsonResponse({"error": "The source has to be UTF-8 text."},
                            status=400)

    try:
        document = document_format.loads(text)
    except document_format.DocumentParseError as exc:
        # The parser's own sentence, not a generic one: it names the line.
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({"document": document.to_dict()})


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------

@login_required
def document_media_embed(request):
    """An embeddable payload for a vault image or SVG the user can read."""
    try:
        file_pk = int(request.GET.get("file_pk", ""))
    except (TypeError, ValueError):
        return JsonResponse({"error": "file_pk is required."}, status=400)

    vault_file = get_object_or_404(
        accessible_files(request.user, file_types=_MEDIA_TYPES)
        .filter(is_encrypted=False), pk=file_pk)

    try:
        with vault_file.file.open("rb") as fh:
            raw = fh.read()
    except Exception as exc:                           # noqa: BLE001
        return JsonResponse({"error": f"Could not read file: {exc}"}, status=500)

    alt = (vault_file.title or vault_file.key or "image").rsplit(".", 1)[0]
    if vault_file.file_type == "svg":
        return JsonResponse({"kind": "svg", "alt": alt,
                             "payload": clean_svg_markup(
                                 raw.decode("utf-8", errors="replace"))})
    mime, _ = mimetypes.guess_type(vault_file.title or vault_file.key or "")
    return JsonResponse({"kind": "image", "alt": alt,
                         "payload": image_bytes_to_data_uri(raw, mime or "")})


@login_required
@require_POST
def document_media_upload(request):
    """Embed a file dropped into the document, or picked with the file input.

    Bytes go through the server rather than a canvas in the browser: the resize
    policy stays in one place, and an SVG gets sanitised by code that cannot be
    skipped by posting here directly.
    """
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "No file."}, status=400)
    if upload.size > getattr(settings, "CYPRIAN_MAX_UPLOAD_BYTES", 20 * 1024 * 1024):
        return JsonResponse({"error": "That file is too large to embed."},
                            status=413)

    raw = upload.read()
    name = upload.name or "image"
    alt = name.rsplit(".", 1)[0]
    mime, _ = mimetypes.guess_type(name)

    if (mime or "") == "image/svg+xml" or name.lower().endswith(".svg"):
        return JsonResponse({"kind": "svg", "alt": alt,
                             "payload": clean_svg_markup(
                                 raw.decode("utf-8", errors="replace"))})
    if not (mime or "").startswith("image/"):
        return JsonResponse({"error": "Only images and SVGs can be embedded."},
                            status=400)
    return JsonResponse({"kind": "image", "alt": alt,
                         "payload": image_bytes_to_data_uri(raw, mime or "")})


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

@login_required
def document_export_pdf(request, file_pk):
    """The document as a PDF, with a contents page and real page numbers."""
    vault_file = _get_owned_file(request, file_pk)
    document = _read_document(vault_file)

    tariff = price_for(request.user, "cyprian")
    try:
        check_quota(CyprianQuotaPolicy, "cyprian.pdf", 1, request.user)
        check_funds(request.user, tariff, "cyprian.pdf", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        # Plain text, not messages+redirect: this is a download target.
        return HttpResponse(str(exc), status=exc.status_code, content_type="text/plain")

    try:
        raw = render_pdf.render(document)
    except render_pdf.PdfUnavailable as exc:
        # A deployment fact, not something the user can fix by retrying.
        return HttpResponse(str(exc), status=503, content_type="text/plain")

    # Charged after the render succeeds — nothing to refund on a synchronous
    # call that either returns bytes or raised before this line.
    _src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk),
            "source_label": vault_file.title or ""}
    record_usage(CyprianUsageEvent, "cyprian.pdf", 1, request.user, **_src)
    charge(request.user, tariff, "cyprian.pdf", 1, **_src)

    base = slugify(document.title or vault_file.title or "document") or "document"
    response = HttpResponse(raw, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{base}.pdf"'
    return response


# ---------------------------------------------------------------------------
# Contracts — toto.notarius writes its body here
# ---------------------------------------------------------------------------

@login_required
def from_contract(request, file_pk):
    """Open a `.contract`'s body in the writer, and keep them linked.

    notarius owns the contract — the parties, the signatures, the audit trail —
    and it is a poor place to write prose: its body is a textarea holding
    Markdown. Cyprian is the writer, so this is where the body is edited, and
    notarius keeps the buttons that are genuinely its own.

    The companion document is created once and reused, and it remembers which
    contract it belongs to in `meta["contract"]` — which round-trips through the
    format for free, so the link survives a download, an edit by hand and a
    restore from backup.

    The minting, the reuse and the write-back all live in `bridge.py` now; this
    is the entry point and the Markdown conversion, which are the only parts
    that are actually about contracts. See `plugins/cyprian_bridges.py`.
    """
    if not apps.is_installed("toto.notarius"):
        raise Http404("No contracts on this host.")
    from toto.notarius import contract_format

    # Imported here, not at module scope: the bridge module registers a plugin
    # on import and asks the app registry whether notarius is installed, and
    # this module is imported from urls.py. Keeping it lazy means views.py
    # never forces that question at an hour when the registry cannot answer it.
    from .plugins.cyprian_bridges import CONTRACT_META

    contract_file = _owned_file(request, file_pk, types=["contract"])
    raw = _read_raw(contract_file)
    try:
        contract = contract_format.loads(raw)
    except contract_format.ContractParseError as exc:
        raise Http404(str(exc))

    companion = open_document(
        key=CONTRACT_META,
        ref=str(contract_file.pk),
        title=f"{slugify(contract.title or contract_file.title)}-body.xml",
        seed_html=_contract_body_html(contract),
        owner=request.user,
        bucket=contract_file.bucket,
        directory=contract_file.directory,
        document_title=contract.title or "Contract",
    )
    return redirect("cyprian:edit", file_pk=companion.pk)


def _contract_body_html(contract) -> str:
    """A contract's body as HTML the writer can hold.

    Markdown through the same renderer notarius already uses, so what you see in
    the writer is what the contract PDF was already showing.
    """
    from toto.notarius import render as notarius_render

    return sanitize_content(notarius_render._body_html(contract))


# ---------------------------------------------------------------------------
# Saving a rendition into the vault
# ---------------------------------------------------------------------------

def _sibling_name(vault_file, document, suffix: str) -> str:
    base = slugify(document.title or vault_file.title or "document") or "document"
    return f"{base}.{suffix}"


def _asked_target(request, vault_file):
    """(bucket, directory) the export modal chose, or the document's own.

    Ownership goes through vault's `resolve_new_file_target`, the same gate the
    New Document flow uses — no rendition can land in someone else's bucket.
    """
    bucket_id = (request.POST.get("bucket") or "").strip()
    directory_id = (request.POST.get("directory") or "").strip()
    if not bucket_id:
        # "Beside the document" is only a kindness while the document is yours.
        # A bridged document lives in somebody else's bucket — a wiki page's file
        # is held by the project lead — and defaulting there would file a team
        # member's export into a vault they can neither browse nor delete from.
        # Their own bucket is the honest default, and resolve_new_file_target is
        # the same gate the New Document flow uses.
        if vault_file.owner_id != request.user.pk:
            return resolve_new_file_target(request.user, None, None)
        return vault_file.bucket, vault_file.directory
    return resolve_new_file_target(request.user, bucket_id, directory_id or None)


def _asked_watermark(request):
    """(text, image_data_uri) from the export modal, both optional and capped.

    The image must be a data: URI of an image — which is the only form the
    picker produces — and small enough to be a stamp, not a poster. Anything
    else is dropped silently: a bad watermark must not cost the export.
    """
    text = (request.POST.get("watermark") or "").strip()[:80]
    image = (request.POST.get("watermark_image") or "").strip()
    if image and (not image.startswith("data:image/") or len(image) > 2_000_000):
        image = ""
    return text, image


def _asked_name(request, vault_file, document, suffix: str) -> str:
    """The name the writer typed into the save prompt, made safe.

    The editor asks at save time — there is no name field anywhere else — so
    this is the one place a rendition's name enters the system. Slugified like
    every other vault name here, and the extension is OURS: the file IS a pdf or
    an html document, and honouring `report.exe` would be labelling bytes wrongly
    on the writer's own instruction.
    """
    raw = (request.POST.get("name") or "").strip()
    if not raw:
        return _sibling_name(vault_file, document, suffix)
    base = slugify(raw.rsplit(".", 1)[0] if "." in raw else raw)
    return f"{base or 'document'}.{suffix}"


def _save_beside(vault_file, *, name: str, data: bytes, file_type: str, owner,
                 bucket=None, directory=None):
    """Write a rendition into the vault — beside its source by default.

    Beside the document rather than in a renditions folder somewhere: the vault
    is the filesystem here, and a PDF of a report belongs where the report is.
    The export modal can point somewhere else (`bucket`/`directory`, ownership
    already enforced by `resolve_new_file_target`). Overwrites the previous
    rendition at that spot rather than accumulating `report-2.pdf` — exporting
    twice is not two documents.
    """
    if bucket is None:
        bucket, directory = vault_file.bucket, vault_file.directory
    existing = VaultFile.objects.filter(
        bucket=bucket, directory=directory,
        title=name).first()
    target = existing or VaultFile(
        owner=owner, title=name,
        key=_unique_key(slugify(name.rsplit(".", 1)[0]) or "export",
                        bucket),
        file_type=file_type, bucket=bucket,
        directory=directory, is_public=False)
    if existing is None:
        target.save()
    target.file.save(name, ContentFile(data), save=True)
    target.content_hash = target.create_hash()
    target.file_size_bytes = target.file.size
    target.save(update_fields=["content_hash", "file_size_bytes"])
    return target


@login_required
def rendition(request, file_pk):
    """Stream a saved rendition — the link the save modal hands back.

    Through cyprian rather than the media URL: these files are private, and
    MEDIA is served straight off disk by nginx with no idea who is asking. The
    ownership check is the same one every other endpoint here uses.
    """
    vault_file = _owned_file(request, file_pk, types=["pdf", "html"])
    if vault_file.is_encrypted:
        raise Http404("No such file")
    content_type = {"pdf": "application/pdf",
                    "html": "text/html; charset=utf-8"}.get(
                        vault_file.file_type, "application/octet-stream")
    response = FileResponse(vault_file.file.open("rb"), content_type=content_type)
    # inline, because the point of the link is to LOOK at what was produced.
    response["Content-Disposition"] = f'inline; filename="{vault_file.title}"'
    return response


@require_POST
@login_required
def document_save_pdf(request, file_pk):
    """Render the PDF and file it in the vault, beside the document."""
    vault_file = _get_owned_file(request, file_pk)
    document = _read_document(vault_file)
    watermark, watermark_image = _asked_watermark(request)
    bucket, directory = _asked_target(request, vault_file)
    try:
        raw = render_pdf.render(document, watermark=watermark,
                                watermark_image=watermark_image)
    except render_pdf.PdfUnavailable as exc:
        return JsonResponse({"error": str(exc)}, status=503)
    except Exception:                                  # noqa: BLE001
        # A corrupt embedded image (or any other render-time surprise) must
        # come back as a sentence, not a 500: the document itself is fine and
        # still saves — only this export failed.
        return JsonResponse({"error": "The PDF could not be rendered. An "
                             "embedded image may be corrupt — try removing "
                             "the most recently added one."}, status=422)

    saved = _save_beside(vault_file, name=_asked_name(request, vault_file, document, "pdf"),
                         data=raw, file_type="pdf", owner=request.user,
                         bucket=bucket, directory=directory)
    return JsonResponse({
        "name": saved.title, "kind": "PDF",
        "url": reverse("cyprian:rendition", args=[saved.pk])})


@require_POST
@login_required
def document_save_html(request, file_pk):
    """The document as one standalone HTML file, filed in the vault.

    Standalone in the same sense the XML is: the stylesheet is inlined and the
    pictures are already data URIs, so the file opens anywhere — in a browser,
    in an email, on a machine that has never heard of this platform. That is the
    point of exporting HTML at all rather than linking to the reader.

    The body is indented on the way out. Storage keeps it as one line — the
    format wraps it in a single CDATA section and the tests pin that to the byte
    — but a file someone downloads is a file someone may open in an editor, and
    one 40 kB line is not a document you can read.
    """
    vault_file = _get_owned_file(request, file_pk)
    document = _read_document(vault_file)

    html = render_to_string("cyprian/standalone.html", {
        "document": document,
        "body_html": clean_html.pretty(document.anchored_content),
        "document_css": render_pdf.document_css(),
        "katex_css": render_pdf.katex_css(),
    })
    bucket, directory = _asked_target(request, vault_file)
    saved = _save_beside(vault_file, name=_asked_name(request, vault_file, document, "html"),
                         data=html.encode("utf-8"), file_type="html",
                         owner=request.user, bucket=bucket, directory=directory)
    return JsonResponse({
        "name": saved.title, "kind": "HTML",
        "url": reverse("cyprian:rendition", args=[saved.pk])})
