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
"""

from __future__ import annotations

import logging

logger = logging.getLogger("toto.vault")

#: Set on a request whose file removal this module already recorded.
AUDITED_ATTR = "_vault_file_audited"

FILE_TRASHED = "FILE_TRASHED"
FILE_DELETED = "FILE_DELETED"


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


def _record(action: str, vault_file, *, by, request, door: str) -> None:
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
                      "trashed": action == FILE_TRASHED},
        )
    except Exception:  # noqa: BLE001 - the trail must never undo a delete
        logger.exception("Could not append the file removal to the audit trail.")
