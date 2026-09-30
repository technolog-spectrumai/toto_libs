"""Sketch — a hand-drawn SVG editor over vault files.

Each drawing is a single ``vault.VaultFile`` of ``file_type="svg"`` holding an
ordinary SVG document — including ones uploaded from anywhere else, which is a
first-class scenario: the editor decomposes what it recognises into editable
shapes and carries everything else as opaque objects it re-emits verbatim.
There is no model layer and no version history: the file is the single source
of truth (the vault file IS the drawing), and the client keeps a 100-step undo.

Hostile files are refused, never rewritten. The rules live in ``toto.antivirus``
and are reached through :mod:`toto.vault.scanning`, so this app carries no
private copy of them — see :class:`toto.sketch.apps.SketchConfig` for why
antivirus is a hard requirement of this one rather than an optional garnish.
"""
from __future__ import annotations

import hashlib

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext
from django.views import View
from django.views.decorators.http import require_POST

from toto.core import assistant
from toto.ui import PageProcessor
from toto.vault import access, editing, scanning, versions
from toto.vault.models import VaultFile
from toto.vault.views import _unique_file_key, resolve_new_file_target

from toto.sketch.models import SketchQuotaPolicy, SketchUsageEvent


# A save can legitimately carry a cover-cropped background image as a data
# URI (the client caps those at 9M characters), so the ceiling sits above
# that and below anything that could hurt the worker.
SAVE_MAX_BYTES = 10_000_000

#: What a save costs and what plan covers it. The door decides both; the
#: save never refuses for either reason.
METRIC = "sketch.save"
ENTITLEMENT = "sketch"

EMPTY_SVG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" '
    'viewBox="0 0 1920 1080">\n</svg>\n'
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_raw(vault_file: VaultFile) -> str:
    """Raw UTF-8 text of the vault file via a fresh storage handle."""
    with vault_file.file.storage.open(vault_file.file.name, "rb") as fh:
        return fh.read().decode("utf-8")


def _get_readable_file(request, file_pk) -> VaultFile:
    """An ``svg`` vault file this user may open, or 404.

    Permission is the vault's own answer and is NOT restated here. `may_read`
    carries five clauses — superuser, owner, public, bucket owner, directory
    ACL — and this view had two of them, which silently 404'd bucket owners and
    shared-directory members on drawings they plainly may read. That mattered
    little while sketch had its own flat index listing the same two clauses; it
    matters now, because the vault lists with all five and every row there
    links into this view. The list and the page must agree by construction.

    A refusal is Not Found rather than Forbidden — a private file's existence is
    not this app's to disclose.
    """
    vault_file = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk,
        file_type="svg",
    )
    if not access.may_read(request.user, vault_file):
        raise Http404("Not found.")
    return vault_file


def _read_svg_body(request, limit: int) -> bytes:
    """The request body, without Django's 2.5 MB form ceiling.

    `request.body` is checked against DATA_UPLOAD_MAX_MEMORY_SIZE, which this
    host does not set and therefore leaves at 2.5 MB — so the drawings big
    enough to need the higher limit (the ones carrying a background image,
    which the client caps at 9M characters) were exactly the ones that could
    never reach it, dying as a bare 400 before this view ran. `request.read()`
    is not size-checked, so the limit becomes ours to state. Copied from
    cyprian via primula rather than raising the global, which would loosen a
    security-relevant ceiling for every other POST on the host.
    """
    raw = request.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("too-big")
    return raw


def _get_owned_file(request, file_pk) -> VaultFile:
    """An ``svg`` vault file the user owns (required to mutate it).

    Its bucket's clearances come first (2026-09-30): the owner filter alone
    let an owner who lacks their bucket's clearance save and delete there,
    against "no owner bypass". `access.gate_by_bucket` is what the open door's
    `may_read` asks, so a drawing hidden from its reader is missing here too.
    """
    return get_object_or_404(
        access.gate_by_bucket(request.user, VaultFile.objects.select_related(
            "bucket", "directory", "owner").filter(access.local_content_q())),
        pk=file_pk,
        owner=request.user,
        file_type="svg",
    )


def _xml_escape_url(vault_file: VaultFile, request) -> str:
    """The generic raw-XML editor for a refused file — the owner's escape.

    ``editor:xml_display`` filters on ownership itself, so the link is only
    offered when it would work; and on a host without toto.editor's routes it
    simply is not offered.
    """
    if not (request.user.is_authenticated and vault_file.owner_id == request.user.id):
        return ""
    try:
        return reverse("editor:xml_display", args=[vault_file.pk])
    except NoReverseMatch:
        return ""


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class SketchCreateView(LoginRequiredMixin, View):
    """Create a blank drawing in a chosen bucket/directory and open it."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        # The door decides plan and money once, here, before anything exists.
        door = editing.door_for(request.user, entitlement=ENTITLEMENT,
                                metric_code=METRIC,
                                policy_model=SketchQuotaPolicy)
        if not door.open:
            return editing.closed_response(request, door)

        bucket, directory = resolve_new_file_target(
            request.user,
            request.POST.get("bucket_id"),
            request.POST.get("directory_id"),
        )

        raw = (request.POST.get("filename") or "").strip()
        base = raw[:-4] if raw.lower().endswith(".svg") else raw
        base = base.strip() or "untitled-drawing"
        title = f"{base}.svg"
        key = _unique_file_key(slugify(base) or "drawing", bucket)

        data = EMPTY_SVG.encode("utf-8")
        vault_file = VaultFile(
            owner=request.user,
            title=title,
            key=key,
            file_type="svg",
            bucket=bucket,
            directory=directory,
            is_public=False,
        )
        vault_file.file.save(title, ContentFile(data), save=False)
        vault_file.content_hash = hashlib.sha256(data).hexdigest()
        vault_file.file_size_bytes = len(data)
        vault_file.save()
        # A first version, so the first real save has something to be a change
        # from. Never allowed to be the reason creating a drawing fails.
        try:
            versions.save_version(vault_file, author=request.user, label="created")
        except Exception:                                  # noqa: BLE001
            pass

        return redirect(reverse("sketch:edit", args=[vault_file.pk]))


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

class SketchEditView(LoginRequiredMixin, View):
    """The sketch editor for one SVG file (offline; hand-rolled, no library)."""

    login_url = reverse_lazy("core:login")
    template_name = "sketch/edit.html"

    def get(self, request, file_pk):
        vault_file = _get_readable_file(request, file_pk)
        try:
            raw = _read_raw(vault_file)
        except Exception:                                  # noqa: BLE001
            raw = EMPTY_SVG

        verdict = scanning.scan(raw, file_type="svg", filename=vault_file.title)

        # The door AFTER the scan, deliberately: a refused file must still show
        # its refusal to somebody whose plan lapsed. They are not being sold
        # anything, they are being told their file is hostile.
        door = editing.door_for(request.user, vault_file, entitlement=ENTITLEMENT,
                                metric_code=METRIC,
                                policy_model=SketchQuotaPolicy)
        if not door.open:
            return editing.closed_response(request, door)

        # The door answers host, plan, lock, encryption and money. OWNERSHIP is
        # still this app's own question, and it has to be asked here: every
        # write route is owner-only (`_get_owned_file`), so a page that offered
        # Save to a reader of a public drawing would be a button that 404s.
        # `door.as_context()` sets can_edit from the door alone, so this
        # narrowing must come after it.
        can_edit = door.writable and vault_file.owner_id == request.user.id

        context = {
            "drawing": vault_file,
            "save_url": reverse("sketch:save", args=[vault_file.pk]),
            "delete_url": reverse("sketch:delete", args=[vault_file.pk]),
            "index_url": reverse("sketch:index"),
            **door.as_context(),
            "can_edit": can_edit,
            # "" without the assistant, for a reader, or for a drawing
            # whose bucket carries the AI shield — the same degradation every
            # editor has.
            "steven_surface": (assistant.surface_for_file("sketch", vault_file)
                               if can_edit else ""),
        }
        if not verdict.ok:
            # A refused file's content never reaches the page — only the
            # reason and, for the owner, the raw-XML way in. Screening on READ
            # as well as write is deliberate: a file can arrive in the vault
            # from a door that predates the scanner, and this editor renders it
            # inline, in our origin.
            context.update({
                "refusal": verdict,
                "xml_url": _xml_escape_url(vault_file, request),
            })
        else:
            context["content_json"] = raw          # json_script island
            context["config_json"] = {
                "saveUrl": context["save_url"],
                "sourceUrl": reverse("sketch:source", args=[vault_file.pk]),
                "canEdit": can_edit,
                "title": vault_file.title,
                # The hash the client will quote as the base of its first save.
                # Two tabs on one drawing used to overwrite each other silently;
                # this is the value that makes the second one a 409 instead.
                "baseHash": vault_file.content_hash or "",
                # Translated here, delivered as data: a JS string is never a
                # place to render a template tag into.
                "strings": {
                    "saved": gettext("Saved."),
                    "saveFailed": gettext("Could not save."),
                    "boardFull": gettext("The board is full."),
                    "badImage": gettext("That image cannot be used."),
                    "unsaved": gettext("Unsaved changes"),
                    "sourceFailed": gettext("Could not read the source."),
                    "applyFailed": gettext("That is not a drawing this editor can open."),
                    "applied": gettext("Applied from source."),
                    "conflict": gettext("This drawing changed elsewhere. Your work "
                                        "was kept as a version — reload to see both."),
                    "locked": gettext("Someone else is editing this right now."),
                },
            }
        return render(request, self.template_name,
                      PageProcessor().decorate(context, request))


@require_POST
def sketch_save(request, file_pk):
    """Persist the edited SVG back to the vault file — verbatim or not at all.

    The order is the one every editor on this platform follows, and it is fixed:
    ownership, encryption, body, lock, staleness, screening, write, settle. Plan
    and money are NOT decided here — the door decided them when the page opened,
    because refusing a save loses work that exists only in a browser tab.

    ``base_hash`` arrives in a header rather than a body field, unlike primula
    and cyprian. That is not an oversight: those two post a JSON envelope and
    can carry a field inside it, while this body IS the SVG file, byte for byte
    — which is the property `test_save_round_trips_bytes_verbatim` pins. There
    is no envelope to put a field in, and inventing one would mean an extra
    encode on every save and a JSON-escaped body roughly double the size for a
    base64 background. `refuse_if_stale` takes the value as a plain argument and
    does not care where it came from.
    """
    # 401, not @login_required's 302: this endpoint answers a fetch(), and a
    # redirect to an HTML login page is not something the client can read.
    # Primula's save says the same.
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    try:
        data = _read_svg_body(request, SAVE_MAX_BYTES)
    except ValueError:
        return JsonResponse({"error": gettext("Drawing too large.")}, status=413)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return JsonResponse({"error": gettext("Not UTF-8.")}, status=400)

    # The editing lock is the first line, exactly as in cyprian: someone else
    # holding it means this save should never have been attempted. 423 and not
    # 409, because a retry cannot succeed until they leave.
    locked = editing.refuse_if_locked(vault_file, request.user, noun="drawing")
    if locked is not None:
        return locked

    # Optimistic concurrency. Two tabs on one drawing used to overwrite each
    # other in silence — and an SVG cannot be merged any more than cyprian's
    # documents can, so the honest answer is the same one: refuse the write,
    # keep the work as a version, and let a human choose.
    stale = editing.refuse_if_stale(
        vault_file, request.headers.get("X-Base-Hash", ""),
        body=data, author=request.user, noun="drawing")
    if stale is not None:
        return stale

    verdict = scanning.scan(text, file_type="svg", filename=vault_file.title)
    if not verdict.ok:
        scanning.record(vault_file, verdict, user=request.user, door="sketch")
        return JsonResponse(verdict.as_error(), status=400)

    try:
        with vault_file.file.open("w") as f:
            f.write(text)
        # Hash/size from the bytes just written — the FieldFile is closed once
        # the write-context exits, so we can't re-read it here.
        vault_file.content_hash = hashlib.sha256(data).hexdigest()
        vault_file.file_size_bytes = len(data)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
        scanning.record(vault_file, verdict, user=request.user, door="sketch")
    except Exception as exc:                               # noqa: BLE001
        return JsonResponse({"error": str(exc)}, status=500)

    # One save, one version, one meter. `settle` never raises: an empty balance
    # comes back as a warning beside a save that landed, never as a lost drawing.
    settled = editing.settle(vault_file, request.user, metric_code=METRIC,
                             event_model=SketchUsageEvent,
                             policy_model=SketchQuotaPolicy)

    # The new hash goes back so the client can send it as the base of its NEXT
    # save. Without that the conflict check above would compare against a stale
    # value and fire on the writer's own second save.
    return JsonResponse({"status": "ok",
                         "content_hash": vault_file.content_hash,
                         **settled})


@login_required
def sketch_source(request, file_pk):
    """The drawing as XML, and back again.

    GET hands back the file's own bytes. POST does NOT write: it screens the
    text and says yes or no, and the client — which owns the only SVG parser
    this app has — decomposes it into shapes and seeds its undo history. That
    is the one place this departs from cyprian's source view, where the parser
    is on the server; the reason is simply that there is no Python model of an
    SVG here and inventing a second one to validate against would create the
    parser differential the scanner exists to avoid.

    The screening still happens on the server, which is the part that matters:
    a client that skipped it would be refused again by ``sketch_save``.
    """
    vault_file = _get_readable_file(request, file_pk)

    if request.method == "GET":
        try:
            raw = _read_raw(vault_file)
        except Exception:                                  # noqa: BLE001
            raw = EMPTY_SVG
        return HttpResponse(raw, content_type="text/plain; charset=utf-8")

    if request.method != "POST":
        return JsonResponse({"error": "GET or POST."}, status=405)
    if vault_file.owner_id != request.user.id:
        return JsonResponse({"error": "Not yours to edit."}, status=403)

    try:
        data = _read_svg_body(request, SAVE_MAX_BYTES)
    except ValueError:
        return JsonResponse({"error": gettext("Drawing too large.")}, status=413)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return JsonResponse({"error": gettext("Not UTF-8.")}, status=400)

    verdict = scanning.scan(text, file_type="svg", filename=vault_file.title)
    if not verdict.ok:
        return JsonResponse(verdict.as_error(), status=400)
    return JsonResponse({"ok": True, "svg": text})


@login_required
@require_POST
def sketch_delete(request, file_pk):
    """Move a drawing (its vault file) to the trash (2026-10-01). Owner only."""
    from toto.vault.trash import remove_file

    vault_file = _get_owned_file(request, file_pk)
    remove_file(vault_file, by=request.user, request=request, door="sketch_delete")
    return redirect(reverse("sketch:index"))
