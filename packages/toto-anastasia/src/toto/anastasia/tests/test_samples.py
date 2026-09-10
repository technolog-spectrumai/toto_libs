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
