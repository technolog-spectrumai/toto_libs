"""Permanent, driver-aware deletion of a stored file.

The ``post_delete`` signal only unlinks local paths — ``instance.file.path``
does not exist for a file whose bytes live in an S3 or remote-toto bucket, so
a bare ``VaultFile.delete()`` leaves the remote object orphaned. This is the
one place that deletes both halves in the right order: the database row first
(inside the caller's transaction, so a ``ProtectedError`` from an app that
pinned the file aborts before anything physical happens), the blob second.

Two modes:

- **lenient** (the default — a single file deleted by a peer, a door): the
  blob goes after commit, and a failed blob delete is logged and leaves an
  invisible orphan, which is strictly better than a live row pointing at
  deleted bytes;
- **strict** (``strict=True`` — the bucket purge, ``bucket_lifecycle``): the
  blob goes INSIDE the transaction, through the driver's ``delete_strict``, so
  bytes that cannot be deleted (a key without delete permission, a
  deactivated key, an unwritable disk) raise and the row comes back. A file
  counts as deleted only when its bytes are; the bucket, and the key that
  reaches its objects, stay until they are. (The one window left: bytes
  deleted, then the commit itself fails — the row stays, pointing at nothing,
  and the next confirmation deletes it; deleting is idempotent.)

Either way, the bodies of the file's saved versions (``VersionBlob``) that no
other version cites go too (``versions.drop_orphan_blobs``): the versions
cascade with the file, the blobs have no link back to it, and nothing else
would ever remove them.
"""

from __future__ import annotations

import logging

from django.db import transaction

from .storage_backends import SoftTimeLimitExceeded, get_bucket_storage

logger = logging.getLogger("toto.vault")


def purge_file(vault_file, *, driver=None, strict: bool = False) -> None:
    """Permanently delete one VaultFile: row first, then the stored bytes and
    the version bodies only it cited.

    Raises ``django.db.models.ProtectedError`` when a PROTECT FK still holds
    the file (transcription sources pin theirs); nothing has been deleted in
    that case. There is no undo.

    ``driver`` is a storage driver the caller already built for the file's
    bucket — the bucket purge (``bucket_lifecycle.purge_bucket``) builds ONE
    for the whole bucket, so its sealed credential is opened once and the
    blob deletes still work after the bucket row itself is gone.

    ``strict`` — see the module docstring: bytes that cannot be deleted raise,
    and the row is kept.
    """
    from .versions import version_blob_ids

    bucket = vault_file.bucket
    name = vault_file.file.name if vault_file.file else ""
    blob_ids = version_blob_ids(vault_file)

    with transaction.atomic():
        vault_file.delete()
        if strict:
            if name:
                (driver or get_bucket_storage(bucket)).delete_strict(name)
            if blob_ids:
                from .versions import drop_orphan_blobs

                drop_orphan_blobs(blob_ids, strict=True)
            return
        if name:
            transaction.on_commit(lambda: _delete_blob(bucket, name, driver))
        if blob_ids:
            transaction.on_commit(lambda: _drop_version_blobs(blob_ids))


def _delete_blob(bucket, name: str, driver=None) -> None:
    # A bucketless file is local by definition; get_bucket_storage treats a
    # missing backend as "local" too, so None falls through to the right driver.
    try:
        (driver or get_bucket_storage(bucket)).delete(name)
    except SoftTimeLimitExceeded:
        raise                           # the worker's clock: its caller stops
    except Exception as exc:  # noqa: BLE001 - the row is gone; only log
        logger.warning("vault: could not delete stored bytes %r: %s", name, exc)


def _drop_version_blobs(blob_ids) -> None:
    from .versions import drop_orphan_blobs

    try:
        drop_orphan_blobs(blob_ids)
    except SoftTimeLimitExceeded:
        raise
    except Exception:  # noqa: BLE001 - the row is gone; only log
        logger.warning("vault: could not drop orphaned version bodies %s", sorted(blob_ids),
                       exc_info=True)
