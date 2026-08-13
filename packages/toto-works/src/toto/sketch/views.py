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
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, reverse, reverse_lazy
from django.utils.text import slugify
from django.utils.translation import gettext
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from toto.ui import PageProcessor
from toto.vault import locks, scanning, versions
from toto.vault.models import VaultFile
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)


# A save can legitimately carry a cover-cropped background image as a data
# URI (the client caps those at 9M characters), so the ceiling sits above
# that and below anything that could hurt the worker.
SAVE_MAX_BYTES = 10_000_000

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


def _location(f: VaultFile) -> str:
    loc = f.bucket.name if f.bucket else "—"
    if f.directory:
        loc = f"{loc} / {f.directory.full_path()}"
    return loc


def _get_readable_file(request, file_pk) -> VaultFile:
    """An ``svg`` vault file the user may open (owner or public)."""
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        file_type="svg",
    )
    if not (vf.is_public or (request.user.is_authenticated and vf.owner_id == request.user.id)):
        from django.http import Http404
        raise Http404("Not found.")
    return vf


def _get_owned_file(request, file_pk) -> VaultFile:
    """An ``svg`` vault file the user owns (required to mutate it)."""
    return get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
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
# Index
# ---------------------------------------------------------------------------

class SketchIndexView(LoginRequiredMixin, View):
    """Every SVG the current user can open, with New / Open / Delete."""

    login_url = reverse_lazy("core:login")
    template_name = "sketch/index.html"
    LIST_CAP = 300

    def get(self, request):
        qs = (
            VaultFile.objects.filter(file_type="svg", is_encrypted=False)
            .filter(Q(is_public=True) | Q(owner=request.user))
            .select_related("owner", "bucket", "directory")
            .order_by("-uploaded_at", "title")[: self.LIST_CAP]
        )
        drawings = [
            {
                "pk": f.pk,
                "title": f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": _location(f),
                "is_owner": f.owner_id == request.user.id,
                "edit_url": reverse("sketch:edit", args=[f.pk]),
                "delete_url": reverse("sketch:delete", args=[f.pk]),
            }
            for f in qs
        ]

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "drawings": drawings,
                "buckets_json": buckets_json,
                "directories_json": directories_json,
                "create_url": reverse("sketch:create"),
            },
            request,
        )
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class SketchCreateView(LoginRequiredMixin, View):
    """Create a blank drawing in a chosen bucket/directory and open it."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
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
        can_edit = vault_file.owner_id == request.user.id and not vault_file.is_encrypted
        context = {
            "drawing": vault_file,
            "can_edit": can_edit,
            "save_url": reverse("sketch:save", args=[vault_file.pk]),
            "index_url": reverse("sketch:index"),
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


@csrf_exempt
def sketch_save(request, file_pk):
    """Persist the edited SVG back to the vault file — verbatim or not at all."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    data = request.body or b""
    if len(data) > SAVE_MAX_BYTES:
        return JsonResponse({"error": "Drawing too large."}, status=400)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return JsonResponse({"error": "Not UTF-8."}, status=400)

    # The editing lock is the first line, exactly as in cyprian: someone else
    # holding it means this save should never have been attempted. 423 and not
    # 409, because a retry cannot succeed until they leave.
    if not locks.may_write(vault_file, request.user):
        held = locks.holder_of(vault_file)
        return JsonResponse(
            {"error": gettext("%(who)s is editing this drawing.")
                      % {"who": held.holder},
             "locked_by": held.holder.get_username()}, status=423)

    # Optimistic concurrency. Two tabs on one drawing used to overwrite each
    # other in silence — and an SVG cannot be merged any more than cyprian's
    # documents can, so the honest answer is the same one: refuse the write,
    # keep the work as a version, and let a human choose.
    base_hash = request.headers.get("X-Base-Hash", "")
    if base_hash and vault_file.content_hash and base_hash != vault_file.content_hash:
        rescued = None
        try:
            rescued = versions.save_conflicting_draft(
                vault_file, body=data, author=request.user)
        except Exception:                                  # noqa: BLE001
            pass                                           # never turn a 409 into a 500
        return JsonResponse(
            {"error": gettext("This drawing changed somewhere else since you "
                              "opened it. Your version was kept so nothing is lost."),
             "content_hash": vault_file.content_hash,
             "kept_as_version": rescued.number if rescued else None}, status=409)

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

    # The new hash goes back so the client can send it as the base of its NEXT
    # save. Without that the conflict check above would compare against a stale
    # value and fire on the writer's own second save.
    return JsonResponse({"status": "ok", "content_hash": vault_file.content_hash})


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

    data = request.body or b""
    if len(data) > SAVE_MAX_BYTES:
        return JsonResponse({"error": gettext("Drawing too large.")}, status=400)
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
    """Remove a drawing (its vault file). Owner only."""
    vault_file = _get_owned_file(request, file_pk)
    vault_file.file.delete(save=False)
    vault_file.delete()
    return redirect(reverse("sketch:index"))
