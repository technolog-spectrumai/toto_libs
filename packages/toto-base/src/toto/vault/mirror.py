"""The metadata mirror: a remote bucket's listing, as local stub rows.

Federated listing is mirrored metadata (the user-fixed decision): a refresh
job maintains local :class:`~toto.vault.models.VaultFile` rows with
``origin="mirror"``, so every page render is a plain DB query and no request
ever probes a peer. The visible staleness stamp is
``Bucket.last_refreshed_at``.

**Stubs point at the peer, not at bytes.** ``file.name`` is the REMOTE key,
which makes the download view work unchanged: it asks the bucket driver, the
driver is the peer adapter, and the wire name is the key. ``is_public`` is
always False — the exporting host's ACLs do not cross, so nothing mirrored is
ever republished here.

**Per PAGE, one transaction, rows AND counters together** (the honesty rule
from datalink's stage runs): a refresh killed mid-walk leaves counters that
describe exactly the rows that exist, and the progress a poll reads is never
ahead of the truth.

**Deletion is rows-only.** A mirror row whose remote file disappeared is
deleted here as a plain row delete — never through ``purge_file``, so a
refresh can NEVER issue a DELETE at the peer. (The ``post_delete`` signal
that removes local bytes is naturally inert for stubs: it checks
``os.path.isfile`` on this host's disk, where a stub has nothing.)
"""
from __future__ import annotations

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

#: Skip-list cap, so a poisoned bucket cannot grow a run row without bound.
MAX_SKIPS = 50

#: Mirror-deletion chunk, bounded so the pass never builds one giant IN ().
DELETE_CHUNK = 500


class RefreshStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Done"
    FAILED = "failed", "Failed"


class BucketRefreshRun(models.Model):
    """One walk of a peer's listing. Same run shape as every queued job here
    (antivirus, texlab, steven), so the polling idiom and the stuck-run
    sweeper work unchanged."""

    bucket = models.ForeignKey("vault.Bucket", on_delete=models.CASCADE,
                               related_name="refresh_runs")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.SET_NULL, null=True,
                              blank=True, related_name="bucket_refresh_runs")
    status = models.CharField(max_length=10, choices=RefreshStatus.choices,
                              default=RefreshStatus.PENDING)
    error = models.TextField(blank=True)

    #: The peer's total, learned from the first page. Null until then — a
    #: progress bar with no denominator renders indeterminate, it does not lie.
    total_remote = models.PositiveIntegerField(null=True, blank=True)
    stubs_created = models.PositiveIntegerField(default=0)
    stubs_updated = models.PositiveIntegerField(default=0)
    stubs_deleted = models.PositiveIntegerField(default=0)
    stubs_unchanged = models.PositiveIntegerField(default=0)
    pages_read = models.PositiveIntegerField(default=0)
    #: [{"key": ..., "reason": one sentence}], capped at MAX_SKIPS.
    skips = models.JSONField(default=list, blank=True)

    task_id = models.CharField(max_length=255, blank=True)
    #: Not an FK — toto.workflows must stay optional. Same trade as ScanRun.
    workflow_run_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["bucket", "-created_at"]),
            models.Index(fields=["status"]),
        ]
        verbose_name = "bucket refresh run"

    def __str__(self):
        return f"refresh of {self.bucket_id} · {self.status}"

    @property
    def is_finished(self) -> bool:
        return self.status in (RefreshStatus.SUCCESS, RefreshStatus.FAILED)

    @property
    def stubs_seen(self) -> int:
        return self.stubs_created + self.stubs_updated + self.stubs_unchanged

    def add_skip(self, key: str, reason: str) -> None:
        if len(self.skips) < MAX_SKIPS:
            self.skips.append({"key": key, "reason": reason})


#: The listing fields a stub mirrors, in one place so the upsert and its
#: change-detection cannot disagree.
def _stub_fields(row: dict) -> dict:
    # A peer that has not taken the pxml migration yet keeps sending the legacy
    # spelling, and _upsert_stub re-applies whatever arrives on EVERY refresh —
    # so normalising here, not in a migration, is what makes it stick.
    file_type = row.get("file_type") or "text"
    if file_type == "presentation":
        file_type = "pxml"
    return {
        "title": row.get("title") or row.get("key") or "",
        "file_type": file_type,
        "file_size_bytes": int(row.get("size") or 0),
        "content_hash": row.get("hash") or "",
        "is_encrypted": bool(row.get("is_encrypted")),
    }


def execute_refresh_run(run_id: int):
    """The worker's body: walk the peer's listing, upsert stubs, prune the
    unseen, stamp the bucket. Returns the finished run row."""
    from .models import Bucket
    from .peer_client import PeerClient, PeerError

    run = BucketRefreshRun.objects.select_related(
        "bucket", "bucket__peer").get(pk=run_id)
    if run.is_finished:
        return run
    run.status = RefreshStatus.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    bucket = run.bucket
    peer = bucket.peer if bucket.peer_id else None
    if peer is None or bucket.storage_backend != "remote_toto":
        return _fail(run, "This bucket is not a mounted remote bucket.")

    client = PeerClient(peer)
    seen_keys: set[str] = set()
    try:
        for page in client.iter_pages():
            rows = page.get("files", [])
            with transaction.atomic():
                for row in rows:
                    _upsert_stub(run, bucket, row, seen_keys)
                run.pages_read += 1
                if page.get("total") is not None:
                    run.total_remote = page["total"]
                run.save()
        with transaction.atomic():
            _prune_unseen(run, bucket, seen_keys)
            run.status = RefreshStatus.SUCCESS
            run.finished_at = timezone.now()
            run.save()
            Bucket.objects.filter(pk=bucket.pk).update(
                last_refreshed_at=run.finished_at)
        BucketPeerStamps.pulled(peer)
    except PeerError as exc:
        return _fail(run, str(exc))
    except Exception as exc:  # noqa: BLE001 - the row must say why, whatever broke
        return _fail(run, f"{type(exc).__name__}: {exc}")
    return run


def _upsert_stub(run, bucket, row: dict, seen_keys: set) -> None:
    from .models import FileOrigin, VaultFile

    key = row.get("key") or ""
    if not key:
        run.add_skip("?", "The peer sent a listing row without a key.")
        return
    seen_keys.add(key)
    fields = _stub_fields(row)
    existing = VaultFile.objects.filter(bucket=bucket, key=key).first()
    if existing is None:
        stub = VaultFile(
            owner=bucket.owner, key=key, bucket=bucket,
            origin=FileOrigin.MIRROR, is_public=False, **fields)
        # The remote key IS the wire name the driver dereferences.
        stub.file.name = key
        stub.save()
        run.stubs_created += 1
        return
    if existing.origin != FileOrigin.MIRROR:
        # A native row colliding with a remote key: never overwrite local
        # bytes' metadata with a peer's listing.
        run.add_skip(key, "A native file already uses this key here.")
        return
    changed = [name for name, value in fields.items()
               if getattr(existing, name) != value]
    if not changed:
        run.stubs_unchanged += 1
        return
    for name in changed:
        setattr(existing, name, fields[name])
    existing.save(update_fields=changed)
    run.stubs_updated += 1


def _prune_unseen(run, bucket, seen_keys: set) -> None:
    from .models import FileOrigin, VaultFile

    stale_pks = [
        pk for pk, key in VaultFile.objects.filter(
            bucket=bucket, origin=FileOrigin.MIRROR).values_list("pk", "key")
        if key not in seen_keys
    ]
    for start in range(0, len(stale_pks), DELETE_CHUNK):
        chunk = stale_pks[start:start + DELETE_CHUNK]
        VaultFile.objects.filter(pk__in=chunk).delete()
    run.stubs_deleted = len(stale_pks)


def _fail(run, reason: str):
    run.status = RefreshStatus.FAILED
    run.error = reason
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
    return run


class BucketPeerStamps:
    """Pull bookkeeping, kept out of the walk so a failed run stamps nothing."""

    @staticmethod
    def pulled(peer) -> None:
        from django.db.models import F

        from .peering import BucketPeer

        BucketPeer.objects.filter(pk=peer.pk).update(
            last_pull_at=timezone.now(), pull_count=F("pull_count") + 1)


def run_payload(run: BucketRefreshRun) -> dict:
    """The polling shape — the same idiom antivirus, texlab and steven use."""
    return {
        "status": run.status,
        "finished": run.is_finished,
        "ok": run.status != RefreshStatus.FAILED,
        "error": run.error,
        "total_remote": run.total_remote,
        "stubs_created": run.stubs_created,
        "stubs_updated": run.stubs_updated,
        "stubs_deleted": run.stubs_deleted,
        "stubs_unchanged": run.stubs_unchanged,
        "stubs_seen": run.stubs_seen,
        "pages_read": run.pages_read,
        "skips": run.skips,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "now": timezone.now().isoformat(),
    }
