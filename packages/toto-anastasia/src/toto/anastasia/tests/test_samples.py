"""Capsule history: throttling, honest nulls, and a retention window.

Unit-level. What matters here is not that a row can be written but that the
table has a ceiling and that "not measured" never charts as zero.
"""

from __future__ import annotations

from django.utils import timezone

from toto.anastasia import samples, services

from .base import SMALL, AnastasiaTestCase


class RecordTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="c", limits=SMALL)

    def test_it_records_a_reading(self):
        report = {"state": "ready", "tier": "kata", "executions_running": 2,
                  "usage": {"ram_mb_used": 128, "pids_used": 9}}
        row = samples.record(self.lease, report, {"bytes": 4096, "files": 12,
                                                  "complete": True})
        self.assertIsNotNone(row)
        self.assertEqual(row.ram_mb_used, 128)
        self.assertEqual(row.storage_bytes, 4096)
        self.assertEqual(row.tier, "kata")

    def test_a_second_reading_too_soon_is_dropped(self):
        """Reconcile runs every 30s. Sampling at that rate is ~2,900 rows per
        capsule per day for a graph nobody reads finer than five minutes."""
        samples.record(self.lease, {"state": "ready"})
        self.assertIsNone(samples.record(self.lease, {"state": "ready"}))
        self.assertEqual(samples.CapsuleSample.objects.count(), 1)

    def test_the_throttle_is_per_capsule(self):
        other = services.reserve(owner=self.user, name="d", limits=SMALL)
        samples.record(self.lease, {"state": "ready"})
        self.assertIsNotNone(samples.record(other, {"state": "ready"}))

    def test_a_later_reading_is_recorded(self):
        past = timezone.now() - timezone.timedelta(
            seconds=samples.MIN_INTERVAL_SECONDS + 60)
        samples.record(self.lease, {"state": "ready"}, now=past)
        self.assertIsNotNone(samples.record(self.lease, {"state": "ready"}))

    def test_an_unmeasured_field_is_NULL_not_zero(self):
        """A zero that meant "unknown" charts as a capsule that emptied
        itself. The same rule monit's snapshot table states."""
        row = samples.record(self.lease, {"state": "degraded"})
        self.assertIsNone(row.storage_bytes)
        self.assertIsNone(row.ram_mb_used)
        self.assertIsNone(row.storage_complete)

    def test_an_incomplete_storage_walk_is_recorded_as_such(self):
        row = samples.record(self.lease, {}, {"bytes": 10, "files": 1,
                                              "complete": False})
        self.assertFalse(row.storage_complete)

    def test_it_survives_a_report_that_is_empty_or_none(self):
        """The runtime being unreachable is ordinary. A sampler that raises
        there takes out the reconcile tick that called it."""
        self.assertIsNotNone(samples.record(self.lease, None))


class PruneTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="c", limits=SMALL)

    def test_old_samples_are_dropped(self):
        """A history table with no retention is a disk-full incident with a
        delay fuse."""
        old = timezone.now() - timezone.timedelta(
            days=samples.RETENTION_DAYS + 1)
        samples.record(self.lease, {"state": "ready"}, now=old)
        samples.record(self.lease, {"state": "ready"})
        self.assertEqual(samples.CapsuleSample.objects.count(), 2)

        self.assertEqual(samples.prune(), 1)
        self.assertEqual(samples.CapsuleSample.objects.count(), 1)

    def test_recent_samples_survive(self):
        samples.record(self.lease, {"state": "ready"})
        self.assertEqual(samples.prune(), 0)

    def test_releasing_a_capsule_takes_its_history(self):
        """CASCADE, deliberately: a sample is about a capsule and means
        nothing once the capsule is gone. Keeping orphans would be a slow leak
        of exactly the rows nobody will ever look at."""
        samples.record(self.lease, {"state": "ready"})
        self.lease.delete()
        self.assertEqual(samples.CapsuleSample.objects.count(), 0)


class SeriesTests(AnastasiaTestCase):
    """Reading the history back, for a chart or the JSON endpoint (9.2/9.6)."""

    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="c", limits=SMALL)

    def _at(self, minutes_ago, **fields):
        """A reading placed in the past, bypassing the throttle deliberately.

        `record()` refuses a second reading inside MIN_INTERVAL_SECONDS, which
        is the behaviour PruneTests relies on; a series test needs several
        readings and cares about their ORDER, not about how they were spaced.
        """
        return samples.CapsuleSample.objects.create(
            lease=self.lease,
            taken_at=timezone.now() - timezone.timedelta(minutes=minutes_ago),
            **fields)

    def test_null_stays_null_and_is_never_zero(self):
        """THE TEST THIS CLASS EXISTS FOR.

        Chart.js draws `null` as a gap and `0` as a floor. A sample taken while
        the runtime was unreachable recorded what it could and left the rest
        NULL — coalescing here would draw a capsule that emptied itself, which
        is this module's oldest rule stated at the reading end.
        """
        self._at(30, cpu_percent=40.0, ram_mb_used=512, storage_bytes=1000)
        self._at(20)
        self._at(10, cpu_percent=55.0, ram_mb_used=640, storage_bytes=1200)
        out = samples.series(self.lease, hours=1)
        cpu = next(m for m in out["measures"] if m["key"] == "cpu_percent")
        self.assertEqual(cpu["values"], [40.0, None, 55.0])

    def test_it_reads_oldest_first(self):
        """A chart wants time left to right. The table's own ordering is
        newest-first because every other reader wants the latest row, and
        reversing in a template is how one caller draws it backwards."""
        self._at(10, cpu_percent=3.0)
        self._at(30, cpu_percent=1.0)
        self._at(20, cpu_percent=2.0)
        out = samples.series(self.lease, hours=1)
        cpu = next(m for m in out["measures"] if m["key"] == "cpu_percent")
        self.assertEqual(cpu["values"], [1.0, 2.0, 3.0])

    def test_an_entirely_unmeasured_measure_says_so(self):
        """Not a flat line at zero: `measured` is False, so the card hides the
        line and explains rather than drawing a floor nobody measured."""
        self._at(10, cpu_percent=12.0)
        out = samples.series(self.lease, hours=1)
        disk = next(m for m in out["measures"] if m["key"] == "storage_bytes")
        self.assertFalse(disk["measured"])
        self.assertEqual(disk["values"], [None])
        cpu = next(m for m in out["measures"] if m["key"] == "cpu_percent")
        self.assertTrue(cpu["measured"])

    def test_the_window_is_clamped_to_what_is_retained(self):
        """Asking for a year draws thirty days; the answer says thirty, so a
        page cannot label a month as a year."""
        self.assertEqual(samples.series(self.lease, hours=24 * 365)["hours"],
                         samples.MAX_HOURS)
        # A zero window is not a window; it is an unspecified one, and it
        # falls back to the default day rather than to the one-hour floor. A
        # NEGATIVE window is somebody's arithmetic and clamps to that floor.
        self.assertEqual(samples.series(self.lease, hours=0)["hours"], 24)
        self.assertEqual(samples.series(self.lease, hours=-5)["hours"], 1)

    def test_a_reading_outside_the_window_is_not_in_it(self):
        self._at(10, cpu_percent=1.0)
        self._at(60 * 5, cpu_percent=9.0)
        out = samples.series(self.lease, hours=1)
        self.assertEqual(out["points"], 1)

    def test_it_reads_one_capsule_only(self):
        other = services.reserve(owner=self.user, name="d", limits=SMALL)
        samples.CapsuleSample.objects.create(lease=other, cpu_percent=99.0)
        self._at(5, cpu_percent=1.0)
        out = samples.series(self.lease, hours=1)
        cpu = next(m for m in out["measures"] if m["key"] == "cpu_percent")
        self.assertEqual(cpu["values"], [1.0])

    def test_the_measures_are_declared_not_derived(self):
        """Adding a line to a user's chart is a decision, never a side effect
        of somebody adding a column to the table."""
        out = samples.series(self.lease, hours=1)
        self.assertEqual([m["key"] for m in out["measures"]],
                         ["cpu_percent", "ram_mb_used", "storage_bytes"])

    def test_an_empty_history_is_an_empty_series_not_an_error(self):
        out = samples.series(self.lease)
        self.assertEqual(out["points"], 0)
        self.assertEqual(out["oldest"], "")
        self.assertTrue(all(m["values"] == [] for m in out["measures"]))
