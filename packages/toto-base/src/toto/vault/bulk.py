"""Several files at once from the vault's file list (2026-10-01): "Move to
the trash" and "Move to…" for the selected files.

Each file is checked on its own, by the rules of the single-file door, and
answered on its own — moved (trashed) or refused with the reason — never all
or nothing: one file somebody else locked away must not keep the other
forty where they were. One audit record per file, refusals included, and the
request is marked (``trash.AUDITED_ATTR``) so ``FileAuditMiddleware`` does
not add a second one naming nobody.

* **Move to the trash** — ``DeleteFileView``'s rules: the member's own file,
  in a bucket whose clearances let them read (pessimistic, no owner bypass),
  never a mirror stub; through ``trash.remove_file``. A file of a mounted
  remote bucket cannot wait in a trash (the bytes are the peer's); it is
  deleted at once, as its single door does, and only when the request says
  ``remote=yes`` — which the list's dialog sends after saying so. Without it
  such a file is refused, not deleted.
* **Move to…** — ``MoveFileView``'s rules for a folder of the file's own
  bucket. Into ANOTHER bucket also the bucket's: one the member may write
  (:func:`may_move_into` — theirs, not being deleted, not hidden from them
  by its clearances) and both on this server's disk, where a move is the
  row changing buckets; between storage kinds it would be a transfer, which
  "Copy to bucket" does. The file keeps its title; its key changes when the
  new bucket already has it.
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from . import access
from .trash import AUDITED_ATTR, remove_file

logger = logging.getLogger("toto.vault")

#: Files one request may name. The list sends what is ticked; a bound keeps
#: one POST from walking the whole vault.
MAX_FILES = 500

FILE_MOVED = "FILE_MOVED"
FILE_TRASHED = "FILE_TRASHED"


def _pks(request) -> list[int] | None:
    """The ``files`` the request names, in order, once each; None when one
    is not a pk or there are too many."""
    raw = request.POST.getlist("files")
    pks: list[int] = []
    for value in raw:
        value = (value or "").strip()
        if not (value.isascii() and value.isdigit()) or len(value) > 12:
            return None
        pk = int(value)
        if pk not in pks:
            pks.append(pk)
    if len(pks) > MAX_FILES:
        return None
    return pks


def _own_files(user, pks):
    """{pk: file} for the ``pks`` the single-file doors would find: the
    member's own live files, gated by their buckets' clearances."""
    from .models import VaultFile

    rows = access.gate_by_bucket(user, VaultFile.objects.filter(owner=user, pk__in=pks))
    return {f.pk: f for f in rows.select_related("bucket")}


def _refused(pk, reason, *, action, request, door, code):
    _record(action, pk, bucket_id=None, request=request, door=door,
            success=False, extra={"refused": code})
    return {"id": pk, "status": "refused", "reason": reason}


def _record(action, pk, *, bucket_id, request, door, success=True, extra=None):
    from django.apps import apps

    setattr(request, AUDITED_ATTR, True)
    if not apps.is_installed("toto.audit"):
        return
    from toto.audit import record

    try:
        record(
            action,
            app_label="vault",
            object_type="VaultFile",
            object_id=str(pk),
            description=f"vault:{door}",
            actor_user=request.user,
            request=request,
            source="vault",
            success=success,
            # Ids only — never a title: the chain is kept forever.
            metadata={"door": door, "bucket_id": bucket_id, **(extra or {})},
        )
    except Exception:  # noqa: BLE001 - the trail must never undo the act
        logger.exception("Could not append the bulk file act to the audit trail.")


def _answer(results, **extra):
    done = sum(1 for r in results if r["status"] != "refused")
    return JsonResponse({"ok": True, "results": results, "done": done,
                         "refused": len(results) - done, **extra})


# ---------------------------------------------------------------------------
# Move to the trash
# ---------------------------------------------------------------------------


@login_required
@require_POST
def bulk_trash(request):
    pks = _pks(request)
    if not pks:
        return JsonResponse({"ok": False, "error": _("Select at least one file.")}, status=400)
    remote_ok = request.POST.get("remote", "") == "yes"
    door = "bulk_trash"
    files = _own_files(request.user, pks)
    results = []
    for pk in pks:
        vault_file = files.get(pk)
        if vault_file is None:
            results.append(_refused(pk, _("Not found, or not yours to delete."),
                                    action=FILE_TRASHED, request=request, door=door,
                                    code="not_found"))
            continue
        if access.is_mirror_row(vault_file):
            results.append(_refused(
                pk, _("Mirrored from another host — delete it on the origin host."),
                action=FILE_TRASHED, request=request, door=door, code="mirror"))
            continue
        if not vault_file.can_be_trashed and not remote_ok:
            results.append(_refused(
                pk, _("Lives on another Zenobia and would be deleted permanently — "
                      "not deleted."),
                action=FILE_TRASHED, request=request, door=door, code="remote"))
            continue
        trashed = remove_file(vault_file, by=request.user, request=request, door=door)
        results.append({"id": pk, "status": "trashed" if trashed else "deleted", "reason": ""})
    return _answer(results)


# ---------------------------------------------------------------------------
# Move to…
# ---------------------------------------------------------------------------


def may_move_into(user, bucket) -> bool:
    """May ``user`` move their files INTO ``bucket`` from another one? The
    rule a new file's target has (``views.resolve_new_file_target``: the
    member's own bucket, not being deleted), the bucket's clearances on top
    (pessimistic, no owner bypass), and the bytes on this server's disk."""
    from toto.socialhub.clearance_access import group_hidden

    from .models import Bucket

    if bucket is None or not getattr(user, "is_authenticated", False):
        return False
    if bucket.owner_id != user.pk or bucket.is_being_deleted or not bucket.is_local:
        return False
    return not group_hidden(user, Bucket.objects.filter(pk=bucket.pk))


def move_targets(user) -> list[dict]:
    """The "Move to…" picker: the buckets a move may name and their folders
    (``[{id, name, into, dirs: [{id, path}]}]``). ``into`` says whether files
    from other buckets may come in (:func:`may_move_into`); a bucket where
    the member only has files of their own is offered for moves inside it."""
    from .models import Bucket, VaultDirectory, VaultFile

    if not getattr(user, "is_authenticated", False):
        return []
    own = [b for b in Bucket.objects.filter(owner=user, deletion_requested_at__isnull=True)
           if may_move_into(user, b)]
    into = {b.pk for b in own}
    holding = set(access.gate_by_bucket(user, VaultFile.objects.filter(
        owner=user, bucket__isnull=False)).values_list("bucket_id", flat=True).distinct())
    buckets = {b.pk: b for b in own}
    for b in Bucket.objects.filter(pk__in=holding - into):
        buckets[b.pk] = b
    dirs: dict[int, list] = {}
    folders = (VaultDirectory.objects.filter(bucket_id__in=list(buckets))
               .select_related("parent").prefetch_related("allowed_users"))
    for d in folders:
        allowed = {u.pk for u in d.allowed_users.all()}
        if allowed and not user.is_superuser and user.pk not in allowed:
            continue
        dirs.setdefault(d.bucket_id, []).append({"id": d.pk, "path": d.full_path()})
    return [{"id": b.pk, "name": b.name, "into": b.pk in into,
             "dirs": sorted(dirs.get(b.pk, []), key=lambda d: d["path"].lower())}
            for b in sorted(buckets.values(), key=lambda b: b.name.lower())]


def _cross_bucket(vault_file, bucket, directory):
    """The row changes buckets: a key free there, the new folder."""
    from django.utils.text import slugify

    from .views import _unique_file_key

    if vault_file.key and not type(vault_file).objects.filter(
            bucket=bucket, key=vault_file.key).exists():
        key = vault_file.key
    else:
        key = _unique_file_key(vault_file.key or slugify(vault_file.title), bucket)
    vault_file.bucket = bucket
    vault_file.directory = directory
    vault_file.key = key
    vault_file.save(update_fields=["bucket", "directory", "key"])


@login_required
@require_POST
def bulk_move(request):
    from django.db import IntegrityError, transaction

    from .models import Bucket, BucketClosed, VaultDirectory

    pks = _pks(request)
    if not pks:
        return JsonResponse({"ok": False, "error": _("Select at least one file.")}, status=400)
    bucket_pk = request.POST.get("destination_bucket", "").strip()
    dir_pk = request.POST.get("destination_directory", "").strip()
    if not (bucket_pk.isascii() and bucket_pk.isdigit()) or (
            dir_pk and not (dir_pk.isascii() and dir_pk.isdigit())):
        return JsonResponse({"ok": False, "error": _("Choose where to move the files.")},
                            status=400)
    bucket = Bucket.objects.filter(pk=bucket_pk).first()
    # A bucket hidden from the member by its clearances is not named back to
    # them: the same answer as one that does not exist.
    if bucket is None or _hidden(request.user, bucket):
        return JsonResponse({"ok": False, "error": _("That destination does not exist.")},
                            status=400)
    directory = None
    if dir_pk:
        directory = VaultDirectory.objects.filter(pk=dir_pk, bucket=bucket).first()
        if directory is None:
            return JsonResponse({"ok": False, "error": _("That destination does not exist.")},
                                status=400)
    door = "bulk_move"
    # From here every file gets its own record (a file already where it was
    # sent, none) — the middleware's one line for the request would name
    # no file. A request refused whole above is left to it.
    setattr(request, AUDITED_ATTR, True)
    files = _own_files(request.user, pks)
    into = None                         # asked once, when a file first needs it
    results = []
    for pk in pks:
        vault_file = files.get(pk)
        if vault_file is None:
            results.append(_refused(pk, _("Not found, or not yours to move."),
                                    action=FILE_MOVED, request=request, door=door,
                                    code="not_found"))
            continue
        if access.is_mirror_row(vault_file):
            results.append(_refused(
                pk, _("Mirrored from another host — move it on the origin host."),
                action=FILE_MOVED, request=request, door=door, code="mirror"))
            continue
        from_bucket = vault_file.bucket_id
        if vault_file.bucket_id == bucket.pk:
            if vault_file.directory_id == (directory.pk if directory else None):
                results.append({"id": pk, "status": "unchanged", "reason": "",
                                "bpk": bucket.pk, "pid": vault_file.directory_id})
                continue
            vault_file.directory = directory
            vault_file.save(update_fields=["directory"])
        else:
            if into is None:
                into = may_move_into(request.user, bucket)
            if not into:
                results.append(_refused(
                    pk, _("Files from other buckets cannot be moved into this one."),
                    action=FILE_MOVED, request=request, door=door, code="not_writable"))
                continue
            if not access.is_local_content(vault_file):
                results.append(_refused(
                    pk, _("Its bytes are not on this server — use Copy to bucket instead."),
                    action=FILE_MOVED, request=request, door=door, code="not_local"))
                continue
            try:
                with transaction.atomic():
                    _cross_bucket(vault_file, bucket, directory)
            except (BucketClosed, IntegrityError):
                vault_file.bucket_id = from_bucket
                results.append(_refused(
                    pk, _("The destination bucket took no file just now; try again."),
                    action=FILE_MOVED, request=request, door=door, code="conflict"))
                continue
        _record(FILE_MOVED, pk, bucket_id=bucket.pk, request=request, door=door,
                extra={"from_bucket_id": from_bucket, "directory_id": vault_file.directory_id})
        results.append({"id": pk, "status": "moved", "reason": "",
                        "bpk": bucket.pk, "pid": vault_file.directory_id})
    return _answer(results)


def _hidden(user, bucket) -> bool:
    from toto.socialhub.clearance_access import group_hidden

    from .models import Bucket

    return group_hidden(user, Bucket.objects.filter(pk=bucket.pk))
