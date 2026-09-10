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

    #: Internet bytes on the capsule's own NIC, accumulated across the runners
    #: that carried them — see `executor/network.py`. These are BYTES TO THE
    #: PROXY, headers and TLS included, not bytes of payload fetched: a request
    #: the proxy refuses still costs the bytes of asking. A capsule with no
    #: egress has no NIC and is NULL here, which is "not measured" and not
    #: "used none".
    #:
    #: MONOTONIC WITHIN A MOUNT and reset by one, so a chart of it is a growth
    #: curve rather than a rate. An executor restart shows as a step down,
    #: because the counter lives in the executor's memory and the runners it
    #: was counting are gone — honest about what happened, and the reason this
    #: is stored per reading rather than derived at read time.
    net_rx_bytes = models.BigIntegerField(null=True, blank=True)
    net_tx_bytes = models.BigIntegerField(null=True, blank=True)

    objects = CapsuleSampleQuerySet.as_manager()

    class Meta:
        verbose_name = "capsule sample"
        indexes = [models.Index(fields=["lease", "-taken_at"],
                                name="anastasia_sample_lease_idx")]

    def __str__(self):
        return f"{self.lease_id} at {self.taken_at:%Y-%m-%d %H:%M}"


def due(lease, *, now=None) -> bool:
    """Whether a reading taken now would be kept.

    The throttle, asked as a question. `record()` applies it anyway; this
    exists so a caller can SKIP THE STORAGE WALK that precedes a reading — the
    beat runs every two minutes and a tree walk it was going to throw away is
    the one cost in the whole sampler worth avoiding.
    """
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(seconds=MIN_INTERVAL_SECONDS)
    return not CapsuleSample.objects.filter(lease=lease,
                                            taken_at__gte=cutoff).exists()


def record(lease, report: dict, storage: dict | None = None, *, now=None):
    """Write a sample, unless one was written recently. Returns it or None.

    Throttled HERE rather than at the caller, because there is more than one
    caller — the reconcile task, a status poll, and anything added later — and
    a throttle each of them has to remember is one that will be forgotten.
    """
    now = now or timezone.now()
    if not due(lease, now=now):
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
        net_rx_bytes=usage.get("net_rx_bytes"),
        net_tx_bytes=usage.get("net_tx_bytes"),
    )


def prune(*, now=None, days: int = RETENTION_DAYS) -> int:
    """Drop samples older than the retention window. Returns how many went."""
    now = now or timezone.now()
    cutoff = now - timezone.timedelta(days=days)
    deleted, _ = CapsuleSample.objects.filter(taken_at__lt=cutoff).delete()
    return deleted


# ---------------------------------------------------------------------------
# Reading the series back (9.2 / 9.6)
# ---------------------------------------------------------------------------

#: What a chart may ask for, and the widest window the table can honestly
#: answer. Asking for more than `RETENTION_DAYS` is not an error — it is
#: clamped, and `series()` reports the window it actually used, so a page that
#: asked for a year does not draw thirty days and label them a year.
MAX_HOURS = RETENTION_DAYS * 24

#: The measures a capsule chart can draw, and the unit each is in.
#:
#: DECLARED RATHER THAN DERIVED from the model's fields: a chart that iterated
#: the columns would start drawing `pids_used` and `executions_running` the day
#: somebody added them to the table, on a page nobody had thought about. This
#: tuple is the contract, and adding a line to it is the decision.
MEASURES = (
    ("cpu_percent", "CPU", "%"),
    ("ram_mb_used", "RAM", "MB"),
    ("storage_bytes", "Disk", "bytes"),
    # DOWNLOADED, not "internet": the name has to say which direction and
    # whose count it is. Both are drawn, because a capsule that has SENT a
    # gigabyte is a different situation from one that has fetched one, and a
    # single "internet" line would hide whichever is the interesting half.
    ("net_rx_bytes", "Downloaded", "bytes"),
    ("net_tx_bytes", "Uploaded", "bytes"),
)


def series(lease, *, hours: int = 24) -> dict:
    """One capsule's history, ready for a chart.

    NULL SURVIVES AS NULL, and that is the whole reason this function exists
    rather than a list comprehension at the call site. A sample taken while the
    runtime was unreachable recorded what it could and left the rest NULL,
    which means "not measured" — and this module's own header says a zero in
    its place "would be charted as a capsule that emptied itself". Chart.js
    draws `null` as a GAP, which is the truth; it draws `0` as a floor, which
    is a lie. So the conversion must not coalesce.

    OLDEST FIRST. The table's index is newest-first because every other reader
    wants the latest row; a chart wants time to run left to right, and
    reversing in the template is how one caller ends up drawing it backwards.
    """
    hours = max(1, min(int(hours or 24), MAX_HOURS))
    # timezone.timedelta, matching record() and prune() above: Django
    # re-exports it and this module never imports datetime.
    cutoff = timezone.now() - timezone.timedelta(hours=hours)
    rows = list(CapsuleSample.objects.filter(lease=lease, taken_at__gte=cutoff)
                .order_by("taken_at"))
    return {
        "hours": hours,
        "points": len(rows),
        # ISO 8601, in UTC, because the browser is the only thing that knows
        # which timezone to show and it can only convert from something
        # unambiguous.
        "taken_at": [r.taken_at.isoformat() for r in rows],
        "measures": [
            {
                "key": key,
                "label": label,
                "unit": unit,
                "values": [getattr(r, key) for r in rows],
                # Whether ANYTHING was measured. A measure that is entirely
                # NULL is not a flat line at zero and must not be drawn as
                # one — the caller hides it and says why.
                "measured": any(getattr(r, key) is not None for r in rows),
            }
            for key, label, unit in MEASURES
        ],
        #: The oldest and newest readings, so a page can say how stale the
        #: picture is. The desk already has that habit for live samples.
        "oldest": rows[0].taken_at.isoformat() if rows else "",
        "newest": rows[-1].taken_at.isoformat() if rows else "",
    }
