"""The worker's body for a transfer run.

Per file, from the cursor: portability check → read via the source driver →
size cap → recompute sha256 → scan on arrival (local dest) → key policy →
land → meter. Each decision that drops a file becomes a skip entry naming
the file and the reason; only a failure of the RUN (dead peer, deleted
bucket, billing collapse) fails the run, and the cursor makes the retry
resume, not restart.

Charging is per landed file with real bytes. Double-copying is prevented by
the cursor (written in the same transaction as the row it advances past);
double-billing one layer lower by the ``vault.transfer.mb:{run}:{src}``
idempotency key on the usage event — a replayed iteration records nothing
twice, whatever happened to the process between cursor writes.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .transfer import TransferRun, TransferStatus

logger = logging.getLogger("toto.vault")

try:  # celery is optional in toto-base; without it nothing sends this
    from celery.exceptions import SoftTimeLimitExceeded
except ImportError:  # pragma: no cover
    class SoftTimeLimitExceeded(Exception):
        pass


def _mb(n_bytes: int) -> Decimal:
    return Decimal(str(n_bytes)) / Decimal("1048576")


def execute_transfer_run(run_id: int) -> TransferRun:
    from . import scanning as _scanning
    from . import storage_backends as _storage_backends
    from .models import FileOrigin, VaultFile
    from .peer_client import PeerClient, PeerError, PeerStatusError
    from .views import _unique_copy_key

    run = TransferRun.objects.select_related(
        "owner", "source_bucket", "source_bucket__peer",
        "dest_bucket", "dest_bucket__peer", "dest_directory").get(pk=run_id)
    if run.is_finished:
        return run
    run.status = TransferStatus.RUNNING
    if run.started_at is None:
        run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    source, dest = run.source_bucket, run.dest_bucket
    if source is None or dest is None:
        return _fail(run, "The source or destination bucket was deleted.")

    try:
        src_driver = _storage_backends.get_bucket_storage(source)
        dest_remote = dest.storage_backend == "remote_toto"
        dst_driver = (None if dest_remote
                      else _storage_backends.get_bucket_storage(dest))
        dest_client = PeerClient(dest.peer) if dest_remote else None
    except RuntimeError as exc:
        return _fail(run, str(exc))

    source_remote = source.storage_backend == "remote_toto"
    tariff = _tariff(run.owner)

    file_ids = list(run.file_ids or [])
    for idx in range(run.cursor, len(file_ids)):
        if _closed(run):
            return run
        src_pk = file_ids[idx]
        source_file = VaultFile.objects.filter(
            pk=src_pk, bucket=source).first()
        if source_file is None:
            _skip(run, idx, f"#{src_pk}",
                  "The source row disappeared before it was copied.")
            continue
        key_label = source_file.key or source_file.title

        # Encrypted non-PDFs are sealed under the holding host's salt: they
        # can move within a host (local↔s3) but never across one.
        crossing = source_remote or dest_remote
        if (crossing and source_file.is_encrypted
                and source_file.file_type != "pdf"):
            _skip(run, idx, key_label,
                  "Encrypted under its host's local salt — not portable.")
            continue

        try:
            content = src_driver.read(source_file.file.name)
        except PeerStatusError as exc:
            if exc.status in (404, 409):
                _skip(run, idx, key_label,
                      f"The origin host refused it: "
                      f"{exc.reason or exc.status}.")
                continue
            return _fail(run, str(exc))
        except PeerError as exc:
            return _fail(run, str(exc))
        except SoftTimeLimitExceeded:
            # The worker's time is up. Not this file's fault, and not the
            # run's: the cursor stands where it is and a resume continues.
            raise
        except Exception as exc:  # noqa: BLE001 - unreadable source = this file's skip
            _skip(run, idx, key_label,
                  f"Could not be read: {type(exc).__name__}: {exc}")
            continue

        if (dest_remote and
                len(content) > _storage_backends.EXTERNAL_UPLOAD_MAX_BYTES):
            cap_mib = _storage_backends.EXTERNAL_UPLOAD_MAX_BYTES // (2 ** 20)
            _skip(run, idx, key_label,
                  f"Too large for a cross-host upload (max {cap_mib} MiB).")
            continue

        # Recompute ALWAYS: bytes that do not match the row's hash are bytes
        # something else changed — copying them under the old hash would
        # launder the change.
        actual_hash = hashlib.sha256(content).hexdigest()
        if (source_file.content_hash
                and actual_hash != source_file.content_hash):
            _skip(run, idx, key_label,
                  "Content hash mismatch — the bytes do not match the row.")
            continue

        verdict = _scanning.Verdict.clean(scanned=False)
        if not dest_remote and _scanning.should_scan(
                source_file.owner, source_file.file_type, door="transfer"):
            verdict = _scanning.scan(content, file_type=source_file.file_type,
                                     filename=source_file.title)
            if not verdict.ok:
                _skip(run, idx, key_label,
                      f"Refused by the scanner ({verdict.reason}).")
                continue

        if dest_remote:
            filename = os.path.basename(source_file.file.name) or key_label
            try:
                reply = dest_client.upload(io.BytesIO(content), filename)
            except PeerStatusError as exc:
                _skip(run, idx, key_label,
                      f"The destination host refused it: "
                      f"{exc.reason or exc.status}.")
                continue
            except PeerError as exc:
                return _fail(run, str(exc))
            far_key = (reply or {}).get("key", "") if isinstance(reply, dict) else ""
            try:
                with transaction.atomic():
                    _advance(run, idx, done=True, n_bytes=len(content))
                    _landed(run, source_file, None, far_key)
            except LandingRefused as exc:
                run.refresh_from_db()
                return _fail(run, str(exc))
        else:
            if run.copy_policy == "fail":
                if VaultFile.objects.filter(
                        bucket=dest, key=source_file.key).exists():
                    _skip(run, idx, key_label,
                          "A file with this key already exists there.")
                    continue
                key = source_file.key
            elif run.copy_policy == "replace":
                key = source_file.key
            else:
                key = _unique_copy_key(source_file, dest)
            try:
                stored_name = dst_driver.save(source_file.file.name, content)
            except SoftTimeLimitExceeded:
                raise
            except Exception as exc:  # noqa: BLE001 - a dead backend fails the RUN
                return _fail(run, f"{type(exc).__name__}: {exc}")
            try:
                with transaction.atomic():
                    if run.copy_policy == "replace":
                        _supersede(dest, key)
                    new_file = VaultFile(
                        owner=source_file.owner,
                        title=source_file.title,
                        key=key,
                        content_hash=actual_hash,
                        file_type=source_file.file_type,
                        is_encrypted=source_file.is_encrypted,
                        is_public=source_file.is_public,
                        notes=source_file.notes,
                        file_size_bytes=len(content),
                        bucket=dest,
                        directory=run.dest_directory,
                        origin=FileOrigin.NATIVE,
                    )
                    new_file.file = stored_name
                    new_file.save()
                    _scanning.record(new_file, verdict, user=run.owner,
                                     door="transfer")
                    _advance(run, idx, done=True, n_bytes=len(content))
                    _landed(run, source_file, new_file, key)
            except LandingRefused as exc:
                # Nothing of this file committed; its bytes go too.
                dst_driver.delete(stored_name)
                run.refresh_from_db()
                return _fail(run, str(exc))
            except BaseException:
                # A soft time limit or a lock timeout stops the landing just
                # as surely, and its bytes are as unowned. The row check is
                # for a commit that went through before an on_commit hook
                # raised: those bytes are the new copy's.
                if not VaultFile.objects.filter(
                        bucket=dest, file=stored_name).exists():
                    dst_driver.delete(stored_name)
                raise

        try:
            _meter(run, src_pk, len(content), tariff)
        except SoftTimeLimitExceeded:
            # The worker's time ran out, not the payer's funds: "Billing
            # failed" here would say the opposite.
            raise
        except Exception as exc:  # noqa: BLE001 - funds ran out mid-run
            # The file already landed; the run stops HERE so the ledger and
            # the rows never drift further apart. Resume re-bills nothing
            # already keyed.
            return _fail(run, f"Billing failed after {run.files_done} "
                              f"file(s): {exc}")

    if _closed(run):
        return run
    with transaction.atomic():
        run.status = TransferStatus.SUCCESS
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])
        if run.files_done:
            from .models import BucketCopyLog

            BucketCopyLog.objects.create(
                from_bucket=source, to_bucket=dest,
                performed_by=run.owner, file_count=run.files_done)
    return run


class LandingRefused(Exception):
    """A landing listener refused a file; the message is the run's error."""


def _landed(run, source_file, new_file, dest_key: str) -> None:
    """Tell listeners a file landed — the last statement of its landing
    transaction, so what they record commits with the row and the cursor, or
    not at all.

    Sent with ``send``, not ``send_robust``: a listener that fails must stop
    the run rather than let a file land that it could not record (yamabiko's
    notebook is the reason this exists). The transaction rolls back, the
    stored bytes are deleted, and the run fails naming the listener's error.
    """
    from .signals import transfer_file_landed

    try:
        transfer_file_landed.send(
            sender=TransferRun, run=run, source_file=source_file,
            new_file=new_file, dest_key=dest_key)
    except SoftTimeLimitExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - any listener failure refuses the file
        raise LandingRefused(
            f"A listener refused {source_file.key or source_file.title}: "
            f"{type(exc).__name__}: {exc}") from exc


def _closed(run) -> bool:
    """Whether somebody else closed this run while it ran — the sweeper, or
    the next yamabiko pass once this one outlived its lease. The runner then
    stops and hands back the run as the closer left it: going on would land
    and bill the same files as whoever took over."""
    if TransferRun.objects.filter(
            pk=run.pk, status=TransferStatus.RUNNING).exists():
        return False
    run.refresh_from_db()
    return True


def _advance(run, idx: int, *, done: bool, n_bytes: int = 0) -> None:
    """Counters + cursor, saved inside the caller's transaction. Only these
    columns: a full save would write this worker's RUNNING over a closer's
    FAILED."""
    run.cursor = idx + 1
    if done:
        run.files_done += 1
        run.bytes_done += n_bytes
    run.save(update_fields=["cursor", "files_done", "bytes_done"])


def _skip(run, idx: int, key: str, reason: str) -> None:
    with transaction.atomic():
        run.add_skip(key, reason, pk=(run.file_ids or [None] * (idx + 1))[idx])
        run.files_skipped += 1
        run.cursor = idx + 1
        run.save(update_fields=["skips", "files_skipped", "cursor"])


def _supersede(bucket, key: str) -> None:
    """Delete the row a 'replace' landing takes the key of — inside the
    landing transaction, as before, so cascades, PROTECT and the landing's
    listeners all see it gone — but its bytes only once that commits.

    A bare delete unlinks a local file at once (the post_delete receiver does
    not wait for the commit), so a landing refused later in the transaction
    brought the old row back pointing at nothing. Blanking ``file`` first
    leaves that receiver nothing to unlink; the rollback restores it."""
    from .models import VaultFile
    from .purge import _delete_blob

    doomed = VaultFile.objects.filter(bucket=bucket, key=key)
    names = [name for name in doomed.values_list("file", flat=True) if name]
    doomed.update(file="")
    doomed.delete()
    for name in names:
        transaction.on_commit(lambda name=name: _delete_blob(bucket, name))


def _fail(run, reason: str):
    run.status = TransferStatus.FAILED
    run.error = reason
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
    return run


def _tariff(user):
    if user is None:
        return None
    from toto.quota.charge import price_for

    return price_for(user, "vault")


def _meter(run, src_pk: int, n_bytes: int, tariff) -> None:
    """Usage + charge for one landed file. The usage event is idempotent by
    (run, source row); the charge follows the gateway's at-most-once shape —
    it is only ever reached in the iteration that advanced the cursor.

    Not ``record_usage``: it answers None both for "already recorded" and for
    "the write failed", and swallows everything on the way. Read as the first,
    the second made a landed file free and ate a soft time limit. Here only
    an EXISTING event means billed. A usage event that cannot be written is
    logged and the file is charged anyway: the cursor has already moved past
    it, so nothing would ever bill it later, and a charge that fails raises
    for the caller ("Billing failed")."""
    if run.owner is None or n_bytes <= 0:
        return
    from django.db import DatabaseError
    from toto.quota.charge import charge

    from .models import VaultUsageEvent

    key = f"vault.transfer.mb:{run.pk}:{src_pk}"
    src = {"source_type": "vault.TransferRun", "source_id": str(run.pk)}
    # A replayed iteration finds its event already recorded and must not bill
    # a second time.
    if VaultUsageEvent.objects.filter(idempotency_key=key).exists():
        return
    try:
        with transaction.atomic():
            VaultUsageEvent.objects.create(
                idempotency_key=key, metric_code="storage.transfer_mb",
                quantity=_mb(n_bytes), unit="MB", user=run.owner, **src)
    except DatabaseError:
        logger.exception("vault: usage event %s not recorded; charging anyway", key)
    charge(run.owner, tariff, "storage.transfer_mb", _mb(n_bytes),
           unit="MB", **src)
