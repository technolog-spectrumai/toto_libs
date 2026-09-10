"""A capsule's history, so "is it growing" is answerable.

`CapsuleRuntime.last_sample` is overwritten in place, so the platform can say
what a capsule is doing NOW and nothing about what it was doing an hour ago —
which is the question behind every real one an operator asks. This is the
retained series.

THREE THINGS THAT MAKE OR BREAK A SAMPLES TABLE, all of them about volume:

* **Sampling interval is not the reconcile interval.** Reconcile runs every 30s;
  sampling at that rate is ~2,900 rows per capsule per day, for a graph nobody
  reads at finer than five minutes. `MIN_INTERVAL_SECONDS` throttles writes
  independently of how often the caller asks.
* **It must prune, and pruning must be someone's job.** A history table with no
  retention is a disk-full incident with a delay fuse. `prune()` exists and the
  beat task calls it; if that task is ever removed, this table has to go too.
* **No filenames, ever.** Storage figures here are counts — the same boundary
  `executor/storage.py` draws. A history of what a capsule contained would be a
  worse privacy leak than a live listing, because it persists.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

#: Never write two samples for one capsule closer together than this, however
#: often the caller asks. Five minutes is finer than any graph a person reads
#: and coarse enough that a busy host writes hundreds of rows a day, not tens
#: of thousands.
MIN_INTERVAL_SECONDS = 300

#: How long a series is kept. Long enough to answer "did it grow this week",
#: short enough that the table has a ceiling.
RETENTION_DAYS = 30


class CapsuleSampleQuerySet(models.QuerySet):
    def for_lease(self, lease):
        return self.filter(lease=lease).order_by("-taken_at")

    def since(self, when):
        return self.filter(taken_at__gte=when)


class CapsuleSample(models.Model):
    """One reading of one capsule, kept.

    Deliberately flat and nullable: a sample taken while the runtime was
    unreachable records what it could and leaves the rest NULL. NULL means
    "not measured", never zero — the same rule `toto.monit`'s snapshot table
    states, and for the same reason: a zero that meant "unknown" would be
    charted as a capsule that emptied itself.
    """

    lease = models.ForeignKey("anastasia.ComputeLease",
                              on_delete=models.CASCADE,
                              related_name="samples")
    taken_at = models.DateTimeField(default=timezone.now, db_index=True)

    state = models.CharField(max_length=20, blank=True)
    tier = models.CharField(max_length=20, blank=True)

    ram_mb_used = models.PositiveIntegerField(null=True, blank=True)
    pids_used = models.PositiveIntegerField(null=True, blank=True)
    cpu_percent = models.FloatField(null=True, blank=True)
    executions_running = models.PositiveIntegerField(null=True, blank=True)

    #: Counts only — see the module docstring and `executor/storage.py`.
    storage_bytes = models.BigIntegerField(null=True, blank=True)
    storage_files = models.PositiveIntegerField(null=True, blank=True)
    #: Whether the storage walk saw everything. A partial reading charted as a
    #: whole one is how a capsule appears to shrink.
    storage_complete = models.BooleanField(null=True, blank=True)

    objects = CapsuleSampleQuerySet.as_manager()

    class Meta:
        verbose_name = "capsule sample"
        indexes = [models.Index(fields=["lease", "-taken_at"],
                                name="anastasia_sample_lease_idx")]

    def __str__(self):
        return f"{self.lease_id} at {self.taken_at:%Y-%m-%d %H:%M}"


def record(lease, report: dict, storage: dict | None = None, *, now=None):
    """Write a sample, unless one was written recently. Returns it or None.

    Throttled HERE rather than at the caller, because there is more than one
    caller — the reconcile task, a status poll, and anything added later — and
    a throttle each of them has to remember is one that will be forgotten.
    """
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(seconds=MIN_INTERVAL_SECONDS)
    if CapsuleSample.objects.filter(lease=lease, taken_at__gte=cutoff).exists():
        return None

    usage = (report or {}).get("usage") or {}
    storage = storage or {}
    return CapsuleSample.objects.create(
        lease=lease,
        taken_at=now,
        state=(report or {}).get("state") or "",
        tier=(report or {}).get("tier") or "",
        ram_mb_used=usage.get("ram_mb_used"),
        pids_used=usage.get("pids_used"),
        cpu_percent=usage.get("cpu_percent"),
        executions_running=(report or {}).get("executions_running"),
        storage_bytes=storage.get("bytes"),
        storage_files=storage.get("files"),
        storage_complete=storage.get("complete"),
    )


def prune(*, now=None, days: int = RETENTION_DAYS) -> int:
    """Drop samples older than the retention window. Returns how many went."""
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(days=days)
    deleted, _ = CapsuleSample.objects.filter(taken_at__lt=cutoff).delete()
    return deleted
