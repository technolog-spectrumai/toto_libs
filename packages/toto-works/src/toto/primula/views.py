"""Primula Sheets — a standalone, vault-backed spreadsheet editor.

Each sheet is a single ``vault.VaultFile`` of ``file_type="sheet"`` whose bytes are a
Univer workbook snapshot JSON (see :mod:`toto.primula.sheet_format`). These views never
keep sheet content in the database — the vault file is the single source of truth,
exactly like memo's ``.pml`` presentations. The only DB model is
:class:`~toto.primula.models.SheetVersion`, the primitive versioning: every save appends
a snapshot and the newest :data:`VERSION_CAP` per sheet are kept.
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

from toto.ui import PageProcessor
from toto.vault.models import VaultFile
from toto.vault.views import (
    _unique_file_key,
    new_file_picker_json,
    resolve_new_file_target,
)

from . import sheet_format
from .models import SheetVersion

# Keep the newest this-many snapshots per sheet; older are pruned on each save.
VERSION_CAP = 50


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
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        file_type="sheet",
    )
    if not (vf.is_public or (request.user.is_authenticated and vf.owner_id == request.user.id)):
        raise Http404("Not found.")
    return vf


def _get_owned_file(request, file_pk) -> VaultFile:
    """A ``sheet`` vault file the user owns (required to mutate it)."""
    return get_object_or_404(
        VaultFile.objects.select_related("bucket", "directory", "owner"),
        pk=file_pk,
        owner=request.user,
        file_type="sheet",
    )


def snapshot_version(vault_file: VaultFile, snapshot_text: str, user, note: str = "") -> None:
    """Append a version and prune to the newest :data:`VERSION_CAP` for this sheet."""
    SheetVersion.objects.create(
        sheet_file=vault_file,
        snapshot=snapshot_text,
        created_by=user if getattr(user, "is_authenticated", False) else None,
        note=note[:200],
    )
    keep = list(
        SheetVersion.objects.filter(sheet_file=vault_file)
        .order_by("-created_at", "-id")
        .values_list("pk", flat=True)[:VERSION_CAP]
    )
    SheetVersion.objects.filter(sheet_file=vault_file).exclude(pk__in=keep).delete()


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
            .filter(Q(is_public=True) | Q(owner=request.user))
            .select_related("owner", "bucket", "directory")
            .annotate(version_count=Count("sheet_versions"))
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
                "versions_url": reverse("primula:versions", args=[f.pk]),
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

        snapshot_version(vault_file, text, request.user, note="created")
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
                "versions_url": reverse("primula:versions", args=[vault_file.pk]),
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

    try:
        snapshot = json.loads(request.body or b"{}")
    except (ValueError, TypeError) as exc:
        return JsonResponse({"error": f"Invalid JSON: {exc}"}, status=400)
    if not (isinstance(snapshot, dict) and isinstance(snapshot.get("sheets"), dict)):
        return JsonResponse({"error": "Not a Univer workbook snapshot."}, status=400)

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

    snapshot_version(vault_file, text, request.user)
    return JsonResponse(
        {"status": "ok", "versions": SheetVersion.objects.filter(sheet_file=vault_file).count()}
    )


@login_required
@require_POST
def sheet_delete(request, file_pk):
    """Remove a sheet (its vault file and, by cascade, its versions). Owner only."""
    vault_file = _get_owned_file(request, file_pk)
    vault_file.file.delete(save=False)
    vault_file.delete()
    return redirect(reverse("primula:index"))


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------

class SheetVersionsView(LoginRequiredMixin, View):
    """List a sheet's saved versions; the owner can restore one."""

    login_url = reverse_lazy("core:login")
    template_name = "primula/versions.html"

    def get(self, request, file_pk):
        vault_file = _get_readable_file(request, file_pk)
        is_owner = vault_file.owner_id == request.user.id
        rows = [
            {
                "pk": v.pk,
                "created_at": v.created_at,
                "created_by": v.created_by.username if v.created_by else "—",
                "note": v.note,
                "size": len(v.snapshot),
                "restore_url": reverse("primula:restore", args=[vault_file.pk, v.pk]),
            }
            for v in vault_file.sheet_versions.select_related("created_by").all()[:VERSION_CAP]
        ]
        context = PageProcessor().decorate(
            {
                "sheet": vault_file,
                "versions": rows,
                "is_owner": is_owner,
                "edit_url": reverse("primula:edit", args=[vault_file.pk]),
                "index_url": reverse("primula:index"),
            },
            request,
        )
        return render(request, self.template_name, context)


@login_required
@require_POST
def sheet_restore(request, file_pk, version_pk):
    """Write a chosen version's snapshot back to the sheet, recording a new version."""
    vault_file = _get_owned_file(request, file_pk)
    if vault_file.is_encrypted:
        return HttpResponseForbidden("File is encrypted.")

    version = get_object_or_404(SheetVersion, pk=version_pk, sheet_file=vault_file)
    text = version.snapshot
    data = text.encode("utf-8")
    with vault_file.file.open("w") as f:
        f.write(text)
    vault_file.content_hash = hashlib.sha256(data).hexdigest()
    vault_file.file_size_bytes = len(data)
    vault_file.save(update_fields=["content_hash", "file_size_bytes"])

    snapshot_version(vault_file, text, request.user, note=f"restored from #{version_pk}")
    return redirect(reverse("primula:edit", args=[vault_file.pk]))
