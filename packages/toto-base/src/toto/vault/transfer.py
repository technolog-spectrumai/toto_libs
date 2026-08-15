"""Transfer runs: copying files when at least one end is not this disk.

The synchronous copy loop is right for local→local — a bounded pass over
local bytes inside one transaction. The moment an endpoint is S3 or a peer,
that loop becomes a web worker parked on someone else's latency, billed
work with no record, and a browser spinner that is really a blocked socket.
A TransferRun is the antivirus/texlab run shape for that case: a row first,
a worker, a poll, and honest partial results.

**The cursor is written in the same transaction as the file it advances
past** (datalink's stage-run honesty rule): a run killed anywhere resumes
exactly where the rows say it stopped, and re-runs cannot double-copy.
Double-BILLING is impossible one layer lower: the per-file idempotency key
``vault.transfer.mb:{run.pk}:{src_pk}`` makes the usage event a no-op on
replay whatever the cursor says.

**Partial completion is SUCCESS.** A transfer that lands 40 files and skips
3 (encrypted, oversized, hash-mismatch) did what could be done, and the
skips say why file by file. FAILED means the run itself could not proceed —
a dead peer, a deleted bucket — and retrying resumes at the cursor.

**total_files is frozen at creation, never counted live** — the progress
bar's denominator must not move. ``file_ids`` is frozen with it: a
selection is what the user saw when they pressed the button.
"""
from __future__ import annotations

from django.conf import settings
from django.db import models

#: Skip-list cap, mirroring BucketRefreshRun.
MAX_SKIPS = 50


class TransferStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RUNNING = "running", "Running"
    SUCCESS = "success", "Done"
    FAILED = "failed", "Failed"


class CopyPolicy(models.TextChoices):
    ADD_SUFFIX = "add_suffix", "Add suffix on conflict"
    REPLACE = "replace", "Replace existing"
    FAIL = "fail", "Skip on conflict"


class TransferRun(models.Model):
    """One queued copy between buckets, at least one end non-local."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.SET_NULL, null=True,
                              blank=True, related_name="vault_transfer_runs")
    #: SET_NULL, not CASCADE: a deleted bucket must not silently erase the
    #: record that bytes were moved and billed.
    source_bucket = models.ForeignKey(
        "vault.Bucket", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfers_out")
    dest_bucket = models.ForeignKey(
        "vault.Bucket", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="transfers_in")
    dest_directory = models.ForeignKey(
        "vault.VaultDirectory", on_delete=models.SET_NULL, null=True,
        blank=True, related_name="transfers_in")

    #: The frozen selection: source VaultFile pks, in copy order.
    file_ids = models.JSONField(default=list, blank=True)
    copy_policy = models.CharField(max_length=12, choices=CopyPolicy.choices,
                                   default=CopyPolicy.ADD_SUFFIX)

    status = models.CharField(max_length=10, choices=TransferStatus.choices,
                              default=TransferStatus.PENDING)
    error = models.TextField(blank=True)

    #: Frozen denominator; null only for legacy rows — a null renders the
    #: indeterminate bar, it never fakes a percentage.
    total_files = models.PositiveIntegerField(null=True, blank=True)
    files_done = models.PositiveIntegerField(default=0)
    files_skipped = models.PositiveIntegerField(default=0)
    bytes_done = models.PositiveBigIntegerField(default=0)
    #: What the pre-flight quota/funds check was made against — sizes as the
    #: source rows claimed them, before any byte moved.
    bytes_estimated = models.PositiveBigIntegerField(default=0)
    #: [{"key": ..., "reason": one sentence}], capped at MAX_SKIPS.
    skips = models.JSONField(default=list, blank=True)
    #: Index into file_ids of the NEXT file to process. Written in the same
    #: transaction as the row it advances past.
    cursor = models.PositiveIntegerField(default=0)

    task_id = models.CharField(max_length=255, blank=True)
    #: Not an FK — toto.workflows must stay optional. Same trade as ScanRun.
    workflow_run_id = models.PositiveIntegerField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner", "-created_at"]),
            models.Index(fields=["status"]),
        ]
        verbose_name = "transfer run"

    def __str__(self):
        return (f"transfer {self.source_bucket_id}→{self.dest_bucket_id} "
                f"· {self.status}")

    @property
    def is_finished(self) -> bool:
        return self.status in (TransferStatus.SUCCESS, TransferStatus.FAILED)

    @property
    def percent(self):
        """0–100, or None when the denominator is unknown (indeterminate)."""
        if not self.total_files:
            return None
        done = self.files_done + self.files_skipped
        return min(100, int(100 * done / self.total_files))

    def add_skip(self, key: str, reason: str, pk: int | None = None) -> None:
        """A skip names the file for a human (key) AND for the retry (pk) —
        a retry run is seeded from the remainder plus these pks."""
        if len(self.skips) < MAX_SKIPS:
            entry = {"key": key, "reason": reason}
            if pk is not None:
                entry["pk"] = pk
            self.skips.append(entry)

    def retry_file_ids(self) -> list:
        """What a retry run should attempt: everything the cursor never
        reached, plus everything that was skipped (capped by MAX_SKIPS —
        uncaptured skips are simply not retried, and the panel says so)."""
        remainder = list(self.file_ids or [])[self.cursor:]
        skipped = [s["pk"] for s in self.skips if s.get("pk") is not None]
        return skipped + [pk for pk in remainder if pk not in set(skipped)]


def run_payload(run: TransferRun) -> dict:
    """The polling shape — the same idiom antivirus and the mirror use."""
    from django.utils import timezone

    return {
        "status": run.status,
        "finished": run.is_finished,
        "ok": run.status != TransferStatus.FAILED,
        "error": run.error,
        "total_files": run.total_files,
        "files_done": run.files_done,
        "files_skipped": run.files_skipped,
        "bytes_done": run.bytes_done,
        "percent": run.percent,
        "skips": run.skips,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "now": timezone.now().isoformat(),
    }
