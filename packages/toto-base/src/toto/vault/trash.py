"""The one way a door removes a member's vault file (2026-10-01).

Every delete door a member can reach — the vault's own list and API, the
editor, the sheets, decks, drawings, notebooks, a workspace's file tree, a
pulled git deletion — calls :func:`remove_file`, so they all mean the same
thing: the file goes to the TRASH, its bytes and versions kept for the
restore and still counted against quota and levy. Only where the trash
cannot hold the bytes (``VaultFile.can_be_trashed`` — a mounted remote
bucket names the peer's file) does the door delete at once, as it always did.

Audited here, not by ``FileAuditMiddleware``: the middleware sees only the
url, and a trash must read ``FILE_TRASHED`` on the chain, distinct from a
real ``FILE_DELETED``. A request this module recorded is marked, and the
middleware leaves it alone, so one act is one record. Doors outside the
vault namespace (editor, primula, memo, …) were never on the chain; they are
now.

The Trash page (``trash_views.py``) asks the rest: whose trash a member sees
(:func:`trashed_for`), :func:`restore_file` (``FILE_RESTORED``) and
:func:`purge_trashed` — "Delete for good", through ``purge.purge_file``
(``FILE_PURGED``).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("toto.vault")

#: Set on a request whose file removal this module already recorded.
AUDITED_ATTR = "_vault_file_audited"

FILE_TRASHED = "FILE_TRASHED"
FILE_DELETED = "FILE_DELETED"
FILE_RESTORED = "FILE_RESTORED"
FILE_PURGED = "FILE_PURGED"

#: What a restored file is called when its name is taken where it lands.
RESTORED_SUFFIX = " (restored)"


def remove_file(vault_file, *, by=None, request=None, door: str = "") -> bool:
    """Trash ``vault_file``, or delete it at once where the trash cannot
    hold it. Returns True when it went to the trash.

    ``by`` is who removed it (``trashed_by``; the audit actor), ``request``
    the request it came in on, if any, ``door`` a short name for the trail.
    The caller has already decided the member may delete the file."""
    if vault_file.can_be_trashed:
        vault_file.trash(by)
        _record(FILE_TRASHED, vault_file, by=by, request=request, door=door)
        return True
    # A mounted remote bucket's row names the peer's file: nothing here can
    # hold it for a restore, so it goes at once, the way the doors always did.
    pk = vault_file.pk
    vault_file.file.delete(save=False)
    vault_file.delete()
    vault_file.pk = pk
    _record(FILE_DELETED, vault_file, by=by, request=request, door=door)
    return False


# ---------------------------------------------------------------------------
# The Trash page's half (2026-10-01): who sees a trashed file, the restore,
# and "Delete for good".
# ---------------------------------------------------------------------------


def sees_every_trash(user) -> bool:
    """A superuser on the Superuser plan sees (and acts on) every trashed
    file; everyone else their own. The account alone is not enough — the
    plan system's rule, as on the vault's other superuser doors."""
    from .plan_gate import superuser_plan_holder

    return superuser_plan_holder(user)


def trashed_for(user):
    """The trashed files ``user`` may see: their own, in buckets whose
    clearances let them read (pessimistic — no owner bypass: a member who
    lost the bucket's clearance loses its trash too); every trashed file for
    a superuser on the plan. Newest first."""
    from .access import gate_by_bucket
    from .models import VaultFile

    rows = VaultFile.all_objects.filter(trashed_at__isnull=False)
    if not sees_every_trash(user):
        if not getattr(user, "is_authenticated", False):
            return rows.none()
        rows = gate_by_bucket(user, rows.filter(owner=user))
    return rows.order_by("-trashed_at", "-pk")


def days_left(vault_file, *, now=None) -> int:
    """Whole days until the nightly purge may take the file (0 = due)."""
    import math

    from django.utils import timezone

    from .models import trash_days

    if vault_file.trashed_at is None:
        return trash_days()
    now = now or timezone.now()
    left = (vault_file.trashed_at - now).total_seconds() / 86400 + trash_days()
    return max(0, math.ceil(left))


class RestoreResult:
    """What a restore did, so the page can say it: the folder it went to
    (``None`` = the bucket's root) and whether its name changed (taken
    there)."""

    def __init__(self, directory, *, renamed: bool, title: str):
        self.directory = directory
        self.renamed = renamed
        self.title = title


def _suffixed(title: str, n: int) -> str:
    import os

    stem, ext = os.path.splitext(title)
    if not stem:                      # ".env": the whole name is the stem
        stem, ext = title, ""
    suffix = RESTORED_SUFFIX if n == 1 else f" (restored {n})"
    return f"{stem}{suffix}{ext}"


def _name_taken(vault_file, title: str, key: str, directory) -> bool:
    from .models import VaultFile

    live = VaultFile.objects.exclude(pk=vault_file.pk)
    if live.filter(bucket_id=vault_file.bucket_id, directory=directory, title=title).exists():
        return True
    return bool(key) and live.filter(bucket_id=vault_file.bucket_id, key=key).exists()


def restore_file(vault_file, *, by=None, request=None) -> RestoreResult:
    """Bring ``vault_file`` back from the trash: to the folder it came from,
    or the bucket's root when that folder is gone; under its own name, or
    with ``RESTORED_SUFFIX`` (then ``(restored 2)``, …) when a live file in
    that folder has its name or one in the bucket has its key — the key is
    one per bucket among live files (``vault_one_live_file_per_key``).
    Recorded as ``FILE_RESTORED``. The caller has decided the member may."""
    import os

    from django.db import transaction
    from django.utils.text import slugify

    from .models import VaultDirectory

    # A folder deleted since SET_NULLs ``trashed_from``: such a file cannot
    # be told from one trashed at the root, and both land at the root —
    # which the page says.
    directory = None
    if vault_file.trashed_from_id is not None:
        directory = VaultDirectory.objects.filter(
            pk=vault_file.trashed_from_id, bucket_id=vault_file.bucket_id).first()
    with transaction.atomic():
        title, key = vault_file.title, vault_file.key
        renamed = False
        n = 1
        while _name_taken(vault_file, title, key, directory):
            renamed = True
            title = _suffixed(vault_file.title, n)
            key = (slugify(os.path.splitext(title)[0]) or "file")[:255]
            n += 1
        vault_file.title = title
        vault_file.key = key
        vault_file.directory = directory
        vault_file.trashed_at = None
        vault_file.trashed_by = None
        vault_file.trashed_from = None
        vault_file.save(update_fields=["title", "key", "directory", "trashed_at",
                                       "trashed_by", "trashed_from"])
    _record(FILE_RESTORED, vault_file, by=by, request=request, door="trash_restore",
            extra={"renamed": renamed, "to_root": directory is None})
    return RestoreResult(directory, renamed=renamed, title=title)


def purge_trashed(vault_file, *, by=None, request=None) -> None:
    """"Delete for good": the row, its bytes and the version bodies only it
    cited, through ``purge.purge_file``. Recorded as ``FILE_PURGED``.
    Raises ``ProtectedError`` when an app still pins the file (nothing is
    deleted then). The caller has decided the member may."""
    from .purge import purge_file

    pk = vault_file.pk
    purge_file(vault_file)
    vault_file.pk = pk
    _record(FILE_PURGED, vault_file, by=by, request=request, door="trash_purge")


def _record(action: str, vault_file, *, by, request, door: str, extra=None) -> None:
    from django.apps import apps

    if request is not None:
        setattr(request, AUDITED_ATTR, True)
    if not apps.is_installed("toto.audit"):
        return
    from toto.audit import record

    actor = by if getattr(by, "pk", None) else None
    try:
        record(
            action,
            app_label="vault",
            object_type="VaultFile",
            object_id=str(vault_file.pk or ""),
            description=f"vault:{door}" if door else "vault",
            actor_user=actor,
            request=request,
            source="vault",
            success=True,
            # Ids only — never a title or content: the chain is kept forever.
            metadata={"door": door, "bucket_id": vault_file.bucket_id,
                      "trashed": action == FILE_TRASHED, **(extra or {})},
        )
    except Exception:  # noqa: BLE001 - the trail must never undo a delete
        logger.exception("Could not append the file removal to the audit trail.")
