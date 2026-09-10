"""The reconcile beat task: expiry, the authoritative capsule list, and silence."""

from __future__ import annotations

import datetime

from django.test import override_settings
from django.utils import timezone

from toto.anastasia import choices, services, tasks
from toto.anastasia.models import ComputeLease

from .base import RUNNABLE, SMALL, AnastasiaTestCase, FakeRuntimeBackend


class ReconcileTaskTests(AnastasiaTestCase):
    def test_it_expires_lapsed_reservations(self):
        lease = services.reserve(owner=self.user, name="old", limits=SMALL)
        ComputeLease.objects.filter(pk=lease.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1))
        result = tasks.reconcile()
        self.assertEqual(result["expired"], 1)
        lease.refresh_from_db()
        self.assertIsNotNone(lease.released_at)

    def test_the_capsule_list_is_read_after_expiring(self):
        """A lease that has just lapsed must not be sent as live and then
        destroyed on the next pass."""
        live = services.reserve(owner=self.user, name="live", limits=SMALL)
        dead = services.reserve(owner=self.user, name="dead", limits=SMALL)
        ComputeLease.objects.filter(pk=dead.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1))
        result = tasks.reconcile()
        self.assertEqual(result["known_capsules"], 1)

    def test_a_null_backend_has_nothing_to_reconcile_with(self):
        """Booking is arithmetic that works with no manager at all."""
        services.reserve(owner=self.user, name="g", limits=SMALL)
        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                "toto.anastasia.runtime.NullRuntimeBackend"):
            result = tasks.reconcile()
        self.assertFalse(result["manager"]["configured"])
        # …and refreshing still happened, because it does not need a manager.
        self.assertIn("refreshed", result)

    def test_a_silent_manager_does_not_kill_the_task(self):
        """A beat task that dies on an unreachable peer stops expiring leases
        too, which is how a pool leaks."""
        services.reserve(owner=self.user, name="g", limits=SMALL)

        class Exploding(FakeRuntimeBackend):
            def reconcile(self, known):
                raise RuntimeError("manager is on fire")

        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                "toto.anastasia.tests.test_tasks.Exploding"):
            globals()["Exploding"] = Exploding
            result = tasks.reconcile()
        self.assertTrue(result["manager"]["unreachable"])
        self.assertEqual(result["expired"], 0)

    def test_it_refreshes_mounted_capsules_only(self):
        mounted = services.reserve(owner=self.user, name="up", limits=RUNNABLE)
        services.mount(lease=mounted)
        services.reserve(owner=self.user, name="down", limits=SMALL)

        class Sampling(FakeRuntimeBackend):
            def status(self, lease):
                return {"sample": {"ram_mb_used": 42},
                        "manager_generation": type(self).generation,
                        "mounted": True}

        globals()["Sampling"] = Sampling
        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                "toto.anastasia.tests.test_tasks.Sampling"):
            result = tasks.reconcile()
        self.assertEqual(result["refreshed"], 1)
        runtime = services.runtime_for(mounted)
        self.assertEqual(runtime.last_sample["ram_mb_used"], 42)

    def test_a_manager_that_restarted_marks_the_capsule_dead(self):
        """The reservation survives; the runtime does not. That distinction is
        the whole point of separating the two."""
        lease = services.reserve(owner=self.user, name="up", limits=RUNNABLE)
        services.mount(lease=lease)

        class Successor(FakeRuntimeBackend):
            def status(self, lease):
                return {"sample": {"ram_mb_used": 1},
                        "manager_generation": "a-different-process",
                        "mounted": True}

        globals()["Successor"] = Successor
        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                "toto.anastasia.tests.test_tasks.Successor"):
            tasks.reconcile()

        runtime = services.runtime_for(lease)
        self.assertEqual(runtime.state, choices.DEAD)
        self.assertIn("restarted", runtime.detail)
        lease.refresh_from_db()
        self.assertIsNone(lease.released_at, "the booking must survive")

    def test_a_silent_status_leaves_the_previous_reading_and_its_age(self):
        """Overwriting a sample with an empty one erases exactly the evidence
        that something is wrong."""
        lease = services.reserve(owner=self.user, name="up", limits=RUNNABLE)
        services.mount(lease=lease)
        runtime = services.runtime_for(lease)
        runtime.last_sample = {"ram_mb_used": 99}
        runtime.sampled_at = timezone.now() - datetime.timedelta(hours=2)
        runtime.save()

        services.refresh_runtime(lease)          # FakeRuntimeBackend returns {}
        runtime.refresh_from_db()
        self.assertEqual(runtime.last_sample, {"ram_mb_used": 99})
        self.assertEqual(services.derive_state(runtime), choices.DEGRADED)


class HistoryRecordingTests(AnastasiaTestCase):
    """The beat writes `CapsuleSample`, and is the only thing that does.

    Until 2026-09-11 nothing called `samples.record` or `samples.prune`, so
    the history table shipped empty and unbounded at once.
    """

    def setUp(self):
        super().setUp()
        self.mounted = services.reserve(owner=self.user, name="up",
                                        limits=RUNNABLE)
        services.mount(lease=self.mounted)
        services.reserve(owner=self.user, name="down", limits=SMALL)

    def _reconcile_with(self, backend_cls):
        globals()[backend_cls.__name__] = backend_cls
        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                f"toto.anastasia.tests.test_tasks.{backend_cls.__name__}"):
            return tasks.reconcile()

    def test_a_mounted_capsule_gets_a_reading_with_its_storage(self):
        from toto.anastasia.samples import CapsuleSample

        class Measuring(FakeRuntimeBackend):
            def status(self, lease):
                return {"sample": {"ram_mb_used": 42, "net_rx_bytes": 1000},
                        "manager_generation": type(self).generation,
                        "mounted": True}

            def storage(self, lease):
                return {"bytes": 4096, "files": 3, "complete": True}

        result = self._reconcile_with(Measuring)
        self.assertEqual(result["recorded"], 1)
        row = CapsuleSample.objects.get(lease=self.mounted)
        self.assertEqual(row.ram_mb_used, 42)
        self.assertEqual(row.net_rx_bytes, 1000)
        self.assertEqual(row.storage_bytes, 4096)
        self.assertTrue(row.storage_complete)
        # The unmounted one has nothing to read and gets no row.
        self.assertEqual(CapsuleSample.objects.count(), 1)

    def test_the_throttle_holds_across_ticks(self):
        """Two minutes between beats, five between readings: the second tick
        records nothing and, more to the point, does not walk storage."""
        walks = []

        class Counting(FakeRuntimeBackend):
            def status(self, lease):
                return {"sample": {"ram_mb_used": 1},
                        "manager_generation": type(self).generation,
                        "mounted": True}

            def storage(self, lease):
                walks.append(str(lease.uuid))
                return {"bytes": 1, "files": 1, "complete": True}

        self.assertEqual(self._reconcile_with(Counting)["recorded"], 1)
        self.assertEqual(self._reconcile_with(Counting)["recorded"], 0)
        self.assertEqual(len(walks), 1)

    def test_a_backend_without_storage_records_null_not_zero(self):
        from toto.anastasia.samples import CapsuleSample

        class NoStorage(FakeRuntimeBackend):
            def status(self, lease):
                return {"sample": {"ram_mb_used": 7},
                        "manager_generation": type(self).generation,
                        "mounted": True}

        self._reconcile_with(NoStorage)
        row = CapsuleSample.objects.get(lease=self.mounted)
        self.assertEqual(row.ram_mb_used, 7)
        self.assertIsNone(row.storage_bytes)

    def test_a_storage_reading_that_raises_costs_only_the_storage(self):
        from toto.anastasia.samples import CapsuleSample

        class Exploding(FakeRuntimeBackend):
            def status(self, lease):
                return {"sample": {"ram_mb_used": 7},
                        "manager_generation": type(self).generation,
                        "mounted": True}

            def storage(self, lease):
                raise RuntimeError("walk failed")

        result = self._reconcile_with(Exploding)
        self.assertEqual(result["recorded"], 1)
        self.assertIsNone(CapsuleSample.objects.get(lease=self.mounted)
                          .storage_bytes)

    def test_the_tick_prunes(self):
        from django.utils import timezone

        from toto.anastasia import samples

        old = timezone.now() - timezone.timedelta(days=samples.RETENTION_DAYS + 1)
        samples.CapsuleSample.objects.create(lease=self.mounted, taken_at=old)
        result = self._reconcile_with(FakeRuntimeBackend)
        self.assertEqual(result["pruned"], 1)
