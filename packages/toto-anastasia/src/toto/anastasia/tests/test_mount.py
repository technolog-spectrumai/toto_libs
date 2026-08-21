"""Mounting: the separate act, its idempotency, and what unmount destroys."""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from toto.anastasia import choices, execute, services
from toto.anastasia.models import ComputeLease, GearEvent

from .base import RUNNABLE, SMALL, AnastasiaTestCase, FakeRuntimeBackend


class MountTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="thesis",
                                      limits=SMALL)

    def test_reserving_does_not_mount(self):
        """Two acts, not one. A user may hold capacity without running it."""
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)
        self.assertNotIn("mount", [c[0] for c in FakeRuntimeBackend.calls])

    def test_mounting_brings_it_to_ready(self):
        runtime = services.mount(lease=self.lease, actor=self.user)
        self.assertEqual(runtime.state, choices.READY)
        self.assertIsNotNone(runtime.mounted_at)
        self.assertEqual(runtime.manager_generation,
                         FakeRuntimeBackend.generation)
        self.assertIn(("mount", str(self.lease.uuid)), FakeRuntimeBackend.calls)

    def test_mounting_twice_does_not_mount_twice(self):
        services.mount(lease=self.lease)
        services.mount(lease=self.lease)
        mounts = [c for c in FakeRuntimeBackend.calls if c[0] == "mount"]
        self.assertEqual(len(mounts), 1)

    def test_unmount_keeps_the_reservation(self):
        """The point of separating the two: unmounting frees the machine, not
        the booking. The capacity stays yours until you release it."""
        services.mount(lease=self.lease)
        before = services.available()
        services.unmount(lease=self.lease)

        self.lease.refresh_from_db()
        self.assertIsNone(self.lease.released_at)
        self.assertEqual(services.available().as_dict(), before.as_dict())
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)

    def test_unmount_is_idempotent(self):
        services.mount(lease=self.lease)
        services.unmount(lease=self.lease)
        services.unmount(lease=self.lease)
        unmount_events = self.lease.events.filter(kind=GearEvent.UNMOUNT)
        self.assertEqual(unmount_events.count(), 1)

    def test_unmounting_an_unmounted_gear_is_not_an_error(self):
        services.unmount(lease=self.lease)   # never mounted at all
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)

    def test_a_closed_lease_cannot_be_mounted(self):
        services.release(lease=self.lease)
        with self.assertRaises(ValidationError) as caught:
            services.mount(lease=self.lease)
        self.assertEqual(caught.exception.refusal_code, services.LEASE_CLOSED)

    def test_an_expired_lease_cannot_be_mounted(self):
        ComputeLease.objects.filter(pk=self.lease.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.lease.refresh_from_db()
        with self.assertRaises(ValidationError) as caught:
            services.mount(lease=self.lease)
        self.assertEqual(caught.exception.refusal_code, services.LEASE_CLOSED)

    def test_a_failed_mount_is_recorded_as_a_refusal(self):
        FakeRuntimeBackend.fail_mount = True
        with self.assertRaises(ValidationError) as caught:
            services.mount(lease=self.lease, actor=self.user)
        self.assertEqual(caught.exception.refusal_code,
                         services.RUNTIME_UNAVAILABLE)
        event = self.lease.events.filter(kind=GearEvent.MOUNT).first()
        self.assertFalse(event.accepted)
        self.assertEqual(event.refusal_code, services.RUNTIME_UNAVAILABLE)
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)

    def test_the_null_backend_refuses_to_pretend(self):
        """A host with no manager may still book capacity, but it must never
        report a Gear as mounted when nothing was mounted."""
        with override_settings(
                ANASTASIA_RUNTIME_BACKEND=
                "toto.anastasia.runtime.NullRuntimeBackend"):
            with self.assertRaises(ValidationError) as caught:
                services.mount(lease=self.lease)
            self.assertEqual(caught.exception.refusal_code,
                             services.RUNTIME_UNAVAILABLE)
            # …but release still works, because unmount is provably a no-op.
            services.release(lease=self.lease)
        self.lease.refresh_from_db()
        self.assertIsNotNone(self.lease.released_at)

    def test_releasing_unmounts_first(self):
        services.mount(lease=self.lease)
        services.release(lease=self.lease)
        self.assertIn(("unmount", str(self.lease.uuid)),
                      FakeRuntimeBackend.calls)


class DerivedStateTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="g", limits=RUNNABLE)
        services.mount(lease=self.lease)

    def _touch(self, **fields):
        runtime = services.runtime_for(self.lease)
        for key, value in fields.items():
            setattr(runtime, key, value)
        runtime.save()
        return runtime

    def test_a_fresh_sample_with_no_work_is_ready(self):
        runtime = self._touch(sampled_at=timezone.now(),
                              last_sample={"ram_mb_used": 10})
        self.assertEqual(services.derive_state(runtime), choices.READY)

    def test_a_live_execution_makes_it_busy(self):
        execute.submit(lease=self.lease, operation="render_pdf")
        runtime = self._touch(sampled_at=timezone.now())
        self.assertEqual(services.derive_state(runtime), choices.BUSY)

    def test_a_stale_sample_is_degraded_not_ready(self):
        """Reading a silent manager as READY is the "broken probe counted as
        zero" bug. Silence must be visible."""
        runtime = self._touch(
            sampled_at=timezone.now() - datetime.timedelta(hours=1))
        self.assertEqual(services.derive_state(runtime), choices.DEGRADED)

    def test_an_oom_kill_degrades_the_gear(self):
        runtime = self._touch(sampled_at=timezone.now(),
                              last_sample={"oom_kills": 1})
        self.assertEqual(services.derive_state(runtime), choices.DEGRADED)

    def test_a_closed_lease_reads_unmounted_whatever_the_row_says(self):
        runtime = self._touch(sampled_at=timezone.now())
        ComputeLease.objects.filter(pk=self.lease.pk).update(
            released_at=timezone.now())
        runtime.lease.refresh_from_db()
        self.assertEqual(services.derive_state(runtime), choices.UNMOUNTED)


class WarmPolicyTests(AnastasiaTestCase):
    def setUp(self):
        super().setUp()
        self.lease = services.reserve(owner=self.user, name="lab",
                                      limits=SMALL)

    def test_a_batch_family_cannot_be_kept_warm(self):
        """Warmth buys nothing for a family that is cheap to recreate — it
        would only hold the user's own Gear capacity idle."""
        with self.assertRaises(ValidationError) as caught:
            services.set_warm_policy(lease=self.lease, policy={"pdf": 1})
        self.assertIn("cannot be kept warm", str(caught.exception))

    def test_a_warm_policy_that_does_not_fit_the_gear_is_refused_at_set_time(self):
        with self.assertRaises(ValidationError) as caught:
            services.set_warm_policy(lease=self.lease, policy={"python": 4})
        self.assertIn("more than this Gear holds", str(caught.exception))

    def test_a_python_runtime_may_be_kept_warm(self):
        big = services.reserve(owner=self.other, name="big",
                               limits=type(SMALL)(2000, 4096, 4096, 512))
        services.set_warm_policy(lease=big, policy={"python": 1},
                                 actor=self.other)
        big.refresh_from_db()
        self.assertEqual(big.warm_policy, {"python": 1})
        self.assertTrue(big.events.filter(kind=GearEvent.WARM).exists())

    def test_zero_counts_are_dropped_rather_than_stored(self):
        services.set_warm_policy(lease=self.lease, policy={"python": 0})
        self.lease.refresh_from_db()
        self.assertEqual(self.lease.warm_policy, {})

    def test_an_unknown_family_is_refused(self):
        with self.assertRaises(Exception):
            services.set_warm_policy(lease=self.lease, policy={"bitcoin": 1})
