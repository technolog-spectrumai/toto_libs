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
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views import View
from django.views.decorators.http import require_POST

from toto.editor.views import BaseFileDisplayView
from toto.antivirus.sanitize import sanitize_svg as clean_svg_markup
from toto.cyprian.media import image_bytes_to_data_uri
from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault import access, editing
from toto.vault.filetree import accessible_files
from toto.vault.models import VaultFile
from toto.vault.views import new_file_picker_json, resolve_new_file_target

from toto.cyprian import tiptap

from . import ctml
from .models import CyprianQuotaPolicy, CyprianUsageEvent
from .bridge import DocumentBridge, entitlement_for, open_document
from .bridge import may_edit as bridge_may_edit
from .bridge import write_back as _bridge_write_back
from toto.antivirus.sanitize import sanitize_content

#: What a save counts against. Seeded TRACK — see cyprian/metrics.py.
METRIC = "cyprian.save"

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
        return ctml.is_document(_read_raw(vault_file))
    except (FileNotFoundError, UnicodeDecodeError, ValueError):
        return False


def _adopt(vault_file: VaultFile) -> None:
    """Retype a document that is not yet filed as CTML.

    Two populations reach here. A document uploaded by hand as `.xml` still
    types as generic `xml` — and it always will, because migration 0023
    deliberately did NOT rename existing files, so a `.ctml`-less document that
    is downloaded and re-uploaded comes back the same way. And a row the
    migration could not reach — mirrored, remote or encrypted — is still spelled
    `document`.

    Repairing the row the first time cyprian touches it is what makes the vault
    buttons start working without a second migration over everybody's files. It
    is why this helper survived the CTML rename rather than being deleted the
    way memo's sniff was.
    """
    if vault_file.file_type != "ctml":
        VaultFile.objects.filter(pk=vault_file.pk).update(file_type="ctml")
        vault_file.file_type = "ctml"


def _get_owned_file(request, file_pk) -> VaultFile:
    """A DOCUMENT of this user's. Strict ownership, in the query.

    Still the gate for everything that reads a document out whole or writes a
    file beside it — `document_source` (raw XML, no renderer, no sanitiser).
    That one is deliberately NOT bridged: it is the owner's own escape hatch,
    and it stays owner-only.

    `_open_document` below is the bridged gate, and it covers the two endpoints
    a shared document actually needs: opening the writer, and saving it.
    """
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk, owner=request.user,
        file_type__in=["ctml", "document", "xml"])
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
    inside the file. Read once, parse once, hand both back — where the pair it
    replaced read and parsed the same bytes TWICE on every writer open.

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
        pk=file_pk, file_type__in=["ctml", "document", "xml"])

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
    if not ctml.is_document(raw):
        raise Http404("Not a document.")

    try:
        document = ctml.loads(raw)
    except ctml.DocumentParseError:
        # Corrupt content — start from a blank rather than blowing up the
        # writer. Saving overwrites with valid XML.
        document = ctml.new_document(title=vault_file.title)

    if vault_file.owner_id == request.user.pk:
        _adopt(vault_file)
        return vault_file, document

    # Deliberately AFTER the ownership branch: _adopt writes to the row, and a
    # non-owner should not be able to retype somebody's file by looking at it.
    # A bridged document was minted as file_type="ctml" anyway.
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

        # Plan, lock and money are all decided HERE, on the way in, and never
        # on the save — a save refused for money is work lost from a browser
        # tab. A bridged document is covered by the OWNING app's plan: a wiki
        # page is part of Tasks, not of Documents.
        door = editing.door_for(
            request.user, vault_file,
            entitlement=entitlement_for(vault_file, document),
            metric_code=METRIC, policy_model=CyprianQuotaPolicy)
        if not door.open:
            return editing.closed_response(request, door)

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
            **door.as_context(),
            "config_json": {
                "canEdit": door.writable,
                "filePk": vault_file.pk,
                "contentHash": vault_file.content_hash or "",
                "urls": {
                    "save": reverse("cyprian:save", args=[file_pk]),
                    "source": reverse("cyprian:source", args=[file_pk]),
                    "embed": reverse("cyprian:media_embed"),
                    "upload": reverse("cyprian:media_upload"),
                    # The vault's own delete — reused, not duplicated.
                    "destroy": reverse("vault:delete_file"),
                },
                # The suggestion the save prompt starts from: the file's own
                # name, which is the only name the writer has ever given this
                # document — there is no title field in the editor.
                "renditionBase": slugify(
                    (vault_file.title or "document").rsplit(".", 1)[0]) or "document",
                # Where a rendition goes unless the writer picks otherwise:
                # beside the document, or — for someone editing a document they
                # do not own — nowhere in particular, so the picker asks.
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
    refusal = editing.refuse_if_locked(vault_file, request.user, noun="document")
    if refusal is not None:
        return refusal

    try:
        document = ctml.Document.from_dict(
            payload.get("document") or payload)
        xml = ctml.dumps(document)
    except Exception as exc:                           # noqa: BLE001
        # Parsed once, before the staleness check, so the rescued draft below
        # is the SAME bytes the save would have written. A body the format
        # cannot read is the client's error, not a server fault.
        return JsonResponse({"error": f"Unreadable document: {exc}"}, status=400)
    xml_bytes = xml.encode("utf-8")

    # Optimistic concurrency: the same document can be open twice, and an
    # expired lock lets a second writer in legitimately. Without this the slower
    # writer silently wins. The losing body is kept as a version rather than
    # discarded — these documents cannot be merged, the whole body being one
    # CDATA line, so the honest answer is two versions and a human.
    stale = editing.refuse_if_stale(
        vault_file, payload.get("base_hash"),
        body=xml_bytes, author=request.user, noun="document")
    if stale is not None:
        return stale

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

    # Only now: a failed save is never a charged one. `settle` never raises —
    # an empty balance comes back as a warning beside a save that stood.
    settled = editing.settle(
        vault_file, request.user, metric_code=METRIC,
        event_model=CyprianUsageEvent, policy_model=CyprianQuotaPolicy)

    return JsonResponse({"status": "ok",
                         "content_hash": vault_file.content_hash, **settled})


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
        document = ctml.loads(text)
    except ctml.DocumentParseError as exc:
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



# ---------------------------------------------------------------------------
# The two conversions
#
# HTML and CTML are different formats, and every crossing between them is a
# named action that produces a NEW FILE. Neither one ever rewrites the file it
# was given: a version is captured AFTER a write, so an in-place conversion
# would replace the bytes and leave no version holding the original.
#
# There is deliberately no CTML -> PDF. A document becomes HTML first, visibly,
# and the HTML goes to the renderer — so nobody is surprised by what the PDF
# contains. See toto.aralia's file-service plugin, whose accepted_file_types is
# what enforces it.
# ---------------------------------------------------------------------------

def _convert_target(vault_file, extension: str) -> str:
    """`report.html` -> `report.ctml`, or `report-2.ctml` if that is taken.

    Converting twice gives a second file. It used to adopt a same-named
    document instead, which made sense while conversion was a side effect of
    pressing Edit; now that it is a deliberate action, silently reopening an
    older file is the surprising answer.
    """
    from toto.vault.models import VaultFile

    stem = (vault_file.title or "document").rsplit(".", 1)[0] or "document"
    title, n = f"{stem}{extension}", 1
    while VaultFile.objects.filter(bucket=vault_file.bucket,
                                   directory=vault_file.directory,
                                   title=title).exists():
        n += 1
        title = f"{stem}-{n}{extension}"
    return title


def _write_beside(vault_file, *, title: str, data: bytes, file_type: str, owner):
    """A new file in the same place as its source. Never the source itself."""
    import hashlib

    from django.core.files.base import ContentFile

    from toto.vault.models import VaultFile

    created = VaultFile(
        owner=owner, title=title,
        key=_unique_key(slugify(title.rsplit(".", 1)[0]) or file_type,
                        vault_file.bucket),
        file_type=file_type, bucket=vault_file.bucket,
        directory=vault_file.directory, is_public=False)
    created.save()
    created.file.save(title, ContentFile(data), save=True)
    created.content_hash = hashlib.sha256(data).hexdigest()
    created.file_size_bytes = len(data)
    created.save(update_fields=["content_hash", "file_size_bytes"])
    return created


def _source_of(request, file_pk, *, types):
    """The file to convert: this user's own, of a type that can be converted.

    The OWNER only. Converting reads every byte and writes them into a file the
    actor will own, which is more than `may_read` grants.
    """
    from toto.vault.access import may_read
    from toto.vault.models import VaultFile

    vault_file = get_object_or_404(VaultFile, pk=file_pk)
    if vault_file.owner_id != request.user.pk or not may_read(request.user, vault_file):
        raise Http404
    if vault_file.file_type not in types:
        raise Http404
    if vault_file.is_encrypted:
        raise Http404
    try:
        with vault_file.file.open("rb") as handle:
            return vault_file, handle.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        raise Http404


def _report_context(request, vault_file, report, *, action_url, heading, target):
    return PageProcessor().decorate({
        "source": vault_file,
        "report": report,
        "action_url": action_url,
        "heading": heading,
        "target_title": target,
    }, request)


@login_required
def html_to_ctml(request, file_pk):
    """Convert an HTML page into a CTML document beside it.

    GET shows what the conversion would cost, itemised, and asks. POST writes.
    Nothing is created until somebody has seen the report — which is the whole
    reason this is two steps rather than one.
    """
    from . import conversion

    vault_file, html = _source_of(request, file_pk, types=("html",))
    report = conversion.html_to_ctml(vault_file, html)
    target = _convert_target(vault_file, ".ctml")

    if request.method != "POST":
        return render(request, "cyprian/convert.html", _report_context(
            request, vault_file, report,
            action_url=reverse("cyprian:html_to_ctml", args=[vault_file.pk]),
            heading=_("Convert this page to a CTML document"), target=target))

    created = _write_beside(vault_file, title=target,
                            data=report.text.encode("utf-8"),
                            file_type="ctml", owner=request.user)
    messages.success(request, _(
        "“%(title)s” was created from this page. The HTML file is untouched."
    ) % {"title": created.title})
    return redirect("cyprian:edit", file_pk=created.pk)


@login_required
def ctml_to_html(request, file_pk):
    """Convert a CTML document into an HTML page beside it.

    The step a document takes before it can become a PDF. Aralia accepts HTML
    and only HTML, so this is the visible half of that sequence rather than
    something done for the user behind a button labelled something else.
    """
    from . import conversion

    vault_file, raw = _source_of(request, file_pk, types=("ctml", "document"))
    try:
        document = ctml.loads(raw)
    except ctml.DocumentParseError:
        raise Http404
    report = conversion.ctml_to_html(document)
    target = _convert_target(vault_file, ".html")

    if request.method != "POST":
        return render(request, "cyprian/convert.html", _report_context(
            request, vault_file, report,
            action_url=reverse("cyprian:ctml_to_html", args=[vault_file.pk]),
            heading=_("Convert this document to an HTML page"), target=target))

    created = _write_beside(vault_file, title=target,
                            data=report.text.encode("utf-8"),
                            file_type="html", owner=request.user)
    messages.success(request, _(
        "“%(title)s” was created from this document, which is untouched. "
        "An HTML page is what the PDF renderer accepts."
    ) % {"title": created.title})
    try:
        return redirect(reverse("editor:html_display", args=[created.pk]))
    except NoReverseMatch:
        return redirect("vault:public_list")
