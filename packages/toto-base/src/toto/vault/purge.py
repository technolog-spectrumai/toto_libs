"""Permanent, driver-aware deletion of a stored file.

The ``post_delete`` signal only unlinks local paths — ``instance.file.path``
does not exist for a file whose bytes live in an S3 or remote-toto bucket, so
a bare ``VaultFile.delete()`` leaves the remote object orphaned. This is the
one place that deletes both halves in the right order: the database row first
(inside the caller's transaction, so a ``ProtectedError`` from an app that
pinned the file aborts before anything physical happens), the blob second,
after commit (a failed blob delete leaves an invisible orphan, which is
strictly better than a live row pointing at deleted bytes — both remote
drivers already swallow and log their delete failures).
"""

from __future__ import annotations

import logging

from django.db import transaction

from .storage_backends import get_bucket_storage

logger = logging.getLogger("toto.vault")


def purge_file(vault_file) -> None:
    """Permanently delete one VaultFile: row first, then the stored bytes.

    Raises ``django.db.models.ProtectedError`` when a PROTECT FK still holds
    the file (transcription sources pin theirs); nothing has been deleted in
    that case. There is no undo.
    """
    bucket = vault_file.bucket
    name = vault_file.file.name if vault_file.file else ""

    with transaction.atomic():
        vault_file.delete()
        if name:
            transaction.on_commit(lambda: _delete_blob(bucket, name))


def _delete_blob(bucket, name: str) -> None:
    # A bucketless file is local by definition; get_bucket_storage treats a
    # missing backend as "local" too, so None falls through to the right driver.
    try:
        get_bucket_storage(bucket).delete(name)
    except Exception as exc:  # noqa: BLE001 - the row is gone; only log
        logger.warning("vault: could not delete stored bytes %r: %s", name, exc)
