"""Mounting: the separate act, its idempotency, and what unmount destroys."""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from toto.anastasia import choices, execute, services
from toto.anastasia.models import ComputeLease, CapsuleEvent

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
        unmount_events = self.lease.events.filter(kind=CapsuleEvent.UNMOUNT)
        self.assertEqual(unmount_events.count(), 1)

    def test_unmounting_an_unmounted_capsule_is_not_an_error(self):
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
        event = self.lease.events.filter(kind=CapsuleEvent.MOUNT).first()
        self.assertFalse(event.accepted)
        self.assertEqual(event.refusal_code, services.RUNTIME_UNAVAILABLE)
        self.assertEqual(services.runtime_for(self.lease).state,
                         choices.UNMOUNTED)

    def test_the_null_backend_refuses_to_pretend(self):
        """A host with no manager may still book capacity, but it must never
        report a Capsule as mounted when nothing was mounted."""
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

    def test_only_a_release_purges_the_files_area(self):
        """The lifetime rule: the files area outlives every unmount but the
        one that ends the reservation. Until 2026-09-11 `release` did not say
        so, and a released Capsule's files lingered until the sweeper found
        the orphaned tree."""
        FakeRuntimeBackend.files[str(self.lease.uuid)] = {"kept.txt": b"x"}
        services.mount(lease=self.lease)
        services.unmount(lease=self.lease)
        self.assertNotIn(("purge", str(self.lease.uuid)),
                         FakeRuntimeBackend.calls)
        self.assertIn("kept.txt", FakeRuntimeBackend.files[str(self.lease.uuid)])
        services.release(lease=self.lease)
        self.assertIn(("purge", str(self.lease.uuid)),
                      FakeRuntimeBackend.calls)
        self.assertNotIn(str(self.lease.uuid), FakeRuntimeBackend.files)


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

    def test_an_oom_kill_degrades_the_capsule(self):
        runtime = self._touch(sampled_at=timezone.now(),
                              last_sample={"oom_kills": 1})
        self.assertEqual(services.derive_state(runtime), choices.DEGRADED)

    def test_a_closed_lease_reads_unmounted_whatever_the_row_says(self):
        runtime = self._touch(sampled_at=timezone.now())
        ComputeLease.objects.filter(pk=self.lease.pk).update(
            released_at=timezone.now())
        runtime.lease.refresh_from_db()
        self.assertEqual(services.derive_state(runtime), choices.UNMOUNTED)


# NO WarmPolicyTests CLASS since 2026-09-10. It held six tests over
# `services.set_warm_policy` — a batch family refused warmth, a policy too big
# for its Capsule, a python runtime accepted, zero counts dropped, an unknown
# family refused. The service is deleted: warm pools were removed because a
# fresh sandbox per job makes them meaningless, and they were already
# vestigial (`served_warm` was hardcoded False, no WARM event was ever
# written, and the caller never sent a warm key over the wire).
#
# The claim that REPLACES them is a catalogue-level one and lives where it can
# still fail: test_limits.test_no_family_can_be_kept_warm asserts no family
# carries a `warmable` flag at all, so restoring the field fails loudly.
