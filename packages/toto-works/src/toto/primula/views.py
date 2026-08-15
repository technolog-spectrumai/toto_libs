"""Primula Sheets — a standalone, vault-backed spreadsheet editor.

Each sheet is a single ``vault.VaultFile`` of ``file_type="sheet"`` whose bytes are a
Univer workbook snapshot JSON (see :mod:`toto.primula.sheet_format`). These views never
keep sheet content in the database — the vault file is the single source of truth,
exactly like memo's XML presentations. There is no DB model for content and none for
history either: versions live in :mod:`toto.vault.versions`, shared with cyprian and
memo, so all three editors keep history the same way. Primula used to carry its own
``SheetVersion`` that snapshotted on EVERY save — which is an autosave log, not a
history, and it was the only one of the three apps to have anything at all.
"""

from __future__ import annotations

import hashlib
import json

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files.base import ContentFile
from django.db.models import Count, Q
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.text import slugify
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from toto.core import assistant
from toto.ui import PageProcessor
from toto.quota import QuotaExceeded, check_quota, record_usage
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for
from toto.primula.models import PrimulaQuotaPolicy, PrimulaUsageEvent
from toto.vault import access, locks, versions
from toto.vault.models import VaultFile
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)

from . import sheet_format

#: Ceiling on a saved workbook. Django's DATA_UPLOAD_MAX_MEMORY_SIZE is 2.5 MB
#: and no host raises it, so without _read_json_body below a workbook past that
#: could not be saved AT ALL — the request died before the view ran. cyprian and
#: memo both learned this; primula never did until now.
MAX_SHEET_BYTES = 32 * 1024 * 1024


def _read_json_body(request, limit: int):
    """The request body as JSON, without Django's 2.5 MB form ceiling.

    Same helper cyprian and memo carry, for the same reason: autosave would turn
    a rare failure into a constant one.
    """
    import json as _json

    body = request.body
    if len(body) > limit:
        raise ValueError("too-big")
    try:
        return _json.loads(body or b"{}")
    except (ValueError, TypeError) as exc:
        raise ValueError(str(exc)) from exc


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
    """A ``sheet`` vault file the user may open (owner or public)."""
    vf = get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk,
        file_type="sheet",
    )
    if not (vf.is_public or (request.user.is_authenticated and vf.owner_id == request.user.id)):
        raise Http404("Not found.")
    return vf


def _get_owned_file(request, file_pk) -> VaultFile:
    """A ``sheet`` vault file the user owns (required to mutate it)."""
    return get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner")
        .filter(access.local_content_q()),
        pk=file_pk,
        owner=request.user,
        file_type="sheet",
    )


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class SheetIndexView(LoginRequiredMixin, View):
    """List the sheets the current user can open, with New / Open / Delete."""

    login_url = reverse_lazy("core:login")
    template_name = "primula/index.html"
    LIST_CAP = 300

    def get(self, request):
        qs = (
            VaultFile.objects.filter(file_type="sheet", is_encrypted=False)
            .filter(access.local_content_q())
            .filter(Q(is_public=True) | Q(owner=request.user))
            .select_related("owner", "bucket", "directory")
            .annotate(version_count=Count("versions"))
            .order_by("-uploaded_at", "title")[: self.LIST_CAP]
        )

        sheets = [
            {
                "pk": f.pk,
                "title": f.title,
                "owner": f.owner.username,
                "uploaded": f.uploaded_at,
                "location": _location(f),
                "is_owner": f.owner_id == request.user.id,
                "versions": f.version_count,
                "edit_url": reverse("primula:edit", args=[f.pk]),
                "delete_url": reverse("primula:delete", args=[f.pk]),
            }
            for f in qs
        ]

        buckets_json, directories_json = new_file_picker_json(request.user)
        context = PageProcessor().decorate(
            {
                "sheets": sheets,
                "buckets_json": buckets_json,
                "directories_json": directories_json,
                "create_url": reverse("primula:create"),
            },
            request,
        )
        return render(request, self.template_name, context)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

class SheetCreateView(LoginRequiredMixin, View):
    """Create a blank sheet in a chosen bucket/directory and open it."""

    login_url = reverse_lazy("core:login")

    def post(self, request):
        bucket, directory = resolve_new_file_target(
            request.user,
            request.POST.get("bucket_id"),
            request.POST.get("directory_id"),
        )

        raw = (request.POST.get("filename") or "").strip()
        base = raw[:-5] if raw.lower().endswith(".json") else raw
        base = base.strip() or "untitled-sheet"
        title = f"{base}.json"
        key = _unique_file_key(slugify(base) or "sheet", bucket)

        text = sheet_format.dumps(sheet_format.new_workbook(base))
        data = text.encode("utf-8")

        vault_file = VaultFile(
            owner=request.user,
            title=title,
            key=key,
            file_type="sheet",
            bucket=bucket,
            directory=directory,
            is_public=False,
        )
        vault_file.file.save(title, ContentFile(data), save=False)
        vault_file.content_hash = hashlib.sha256(data).hexdigest()
        vault_file.file_size_bytes = len(data)
        vault_file.save()

        # A new sheet is itself a deliberate act, so it gets a named v1.
        versions.save_version(vault_file, author=request.user,
                              label="created")
        return redirect(reverse("primula:edit", args=[vault_file.pk]))


# ---------------------------------------------------------------------------
# Editor
# ---------------------------------------------------------------------------

class SheetEditView(LoginRequiredMixin, View):
    """The Univer spreadsheet editor for one sheet (offline; vendored bundle)."""

    login_url = reverse_lazy("core:login")
    template_name = "primula/edit.html"

    def get(self, request, file_pk):
        vault_file = _get_readable_file(request, file_pk)
        try:
            raw = _read_raw(vault_file)
        except Exception:
            raw = ""
        try:
            snapshot = sheet_format.loads(raw)
            if not (isinstance(snapshot, dict) and isinstance(snapshot.get("sheets"), dict)):
                raise ValueError
        except (ValueError, TypeError):
            # Corrupt / non-workbook content — open a blank grid; saving overwrites.
            snapshot = sheet_format.new_workbook(vault_file.title or "Sheet")

        can_edit = vault_file.owner_id == request.user.id and not vault_file.is_encrypted
        context = PageProcessor().decorate(
            {
                "sheet": vault_file,
                "snapshot_json": snapshot,
                "can_edit": can_edit,
                "save_url": reverse("primula:save", args=[vault_file.pk]),
                "index_url": reverse("primula:index"),
                # "" without the assistant, and the template renders nothing.
                "steven_surface": assistant.surface_for_file("primula",
                                                             vault_file),
            },
            request,
        )
        return render(request, self.template_name, context)


@csrf_exempt
def sheet_save(request, file_pk):
    """Persist the edited Univer workbook back to the vault file, snapshot a version."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=400)
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Not authenticated."}, status=401)

    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return JsonResponse({"error": "File is encrypted. Decrypt it first."}, status=403)

    # Django's DATA_UPLOAD_MAX_MEMORY_SIZE defaults to 2.5 MB and no host raises
    # it, so reading request.body directly made any workbook past that
    # UNSAVEABLE — it raised before the view ran. cyprian and memo both route
    # around this; primula never did. Same helper, same reasoning.
    try:
        snapshot = _read_json_body(request, MAX_SHEET_BYTES)
    except ValueError as exc:
        if str(exc) == "too-big":
            return JsonResponse(
                {"error": f"That workbook is larger than "
                          f"{MAX_SHEET_BYTES // (1024 * 1024)} MB."}, status=413)
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)
    if not (isinstance(snapshot, dict) and isinstance(snapshot.get("sheets"), dict)):
        return JsonResponse({"error": "Not a Univer workbook snapshot."}, status=400)

    # Primula had NO concurrency control at all: two tabs silently overwrote
    # each other with no error anywhere. It was the one app with versioning and
    # the one app that could lose work without saying so.
    if not locks.may_write(vault_file, request.user):
        held = locks.holder_of(vault_file)
        return JsonResponse(
            {"error": f"{held.holder} is editing this sheet.",
             "locked_by": held.holder.get_username()}, status=423)

    base_hash = snapshot.pop("base_hash", None)
    if base_hash and vault_file.content_hash and base_hash != vault_file.content_hash:
        rescued = None
        try:
            rescued = versions.save_conflicting_draft(
                vault_file, body=sheet_format.dumps(snapshot).encode("utf-8"),
                author=request.user)
        except Exception:                              # noqa: BLE001
            pass
        return JsonResponse(
            {"error": "This sheet changed somewhere else since you opened it. "
                      "Your workbook was kept as a version so nothing is lost.",
             "content_hash": vault_file.content_hash,
             "kept_as_version": rescued.number if rescued else None}, status=409)

    # After the is_authenticated check above, not before: check_funds resolves a
    # billing account off the user, and handing it AnonymousUser raises rather
    # than refusing. The seeded policy is TRACK, so this counts and does not
    # refuse — refusing a save would lose the user's unsaved work, and with no
    # top-up path that is unrecoverable without an admin.
    tariff = price_for(request.user, "primula")
    try:
        check_quota(PrimulaQuotaPolicy, "primula.save", 1, request.user)
        check_funds(request.user, tariff, "primula.save", 1)
    except (QuotaExceeded, InsufficientFunds) as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status_code)

    text = sheet_format.dumps(snapshot)
    data = text.encode("utf-8")
    try:
        with vault_file.file.open("w") as f:
            f.write(text)
        # Hash/size from the bytes just written — the FieldFile is closed once the
        # write-context exits, so we can't re-read it here.
        vault_file.content_hash = hashlib.sha256(data).hexdigest()
        vault_file.file_size_bytes = len(data)
        vault_file.save(update_fields=["content_hash", "file_size_bytes"])
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    # Counted after the write, so a failed save is not a charged one. No
    # idempotency key: saving twice is two saves, which is the point.
    _src = {"source_type": "vault.VaultFile", "source_id": str(vault_file.pk),
            "source_label": vault_file.title or ""}
    record_usage(PrimulaUsageEvent, "primula.save", 1, request.user, **_src)
    charge(request.user, tariff, "primula.save", 1, **_src)

    # No version is cut here. A version is something a person decides to keep
    # and name (toto.vault.versions); snapshotting every autosave produced 50
    # rows of noise per sheet and buried the two saves anyone cared about.
    return JsonResponse({"status": "ok", "content_hash": vault_file.content_hash})


@login_required
@require_POST
def sheet_delete(request, file_pk):
    """Remove a sheet (its vault file and, by cascade, its versions). Owner only."""
    vault_file = _get_owned_file(request, file_pk)
    vault_file.file.delete(save=False)
    vault_file.delete()
    return redirect(reverse("primula:index"))
