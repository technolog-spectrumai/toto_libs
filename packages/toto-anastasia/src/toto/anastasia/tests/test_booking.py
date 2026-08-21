"""Reserving capacity: admission, exhaustion, and the idle-deduction rule."""

from __future__ import annotations

import datetime

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone

from toto.anastasia import choices, services
from toto.anastasia.limits import Limits
from toto.anastasia.models import ComputeLease, GearEvent, GearRuntime

from .base import HALF, POOL, SMALL, AnastasiaTestCase


class ReserveTests(AnastasiaTestCase):
    def test_a_reservation_deducts_from_the_pool_while_idle(self):
        """The rule the whole design rests on.

        Nothing is mounted and nothing is running, yet the pool must show the
        capacity as gone. A booking that only counted while busy would be a
        queue wearing a reservation's clothes.
        """
        before = services.available()
        lease = services.reserve(owner=self.user, name="thesis", limits=HALF)

        self.assertEqual(services.runtime_for(lease).state, choices.UNMOUNTED)
        self.assertFalse(lease.executions.exists())

        after = services.available()
        self.assertEqual(after.cpu_millicores,
                         before.cpu_millicores - HALF.cpu_millicores)
        self.assertEqual(after.ram_mb, before.ram_mb - HALF.ram_mb)
        self.assertEqual(after.scratch_mb, before.scratch_mb - HALF.scratch_mb)
        self.assertEqual(after.pids, before.pids - HALF.pids)

    def test_the_pool_is_exhausted_in_every_dimension_independently(self):
        services.reserve(owner=self.user, name="one", limits=HALF)
        services.reserve(owner=self.user, name="two", limits=HALF)
        self.assertTrue(services.available().is_zero)

        with self.assertRaises(ValidationError) as caught:
            services.reserve(owner=self.other, name="three", limits=SMALL)
        self.assertEqual(caught.exception.refusal_code, services.POOL_EXHAUSTED)

    def test_a_refusal_names_every_dimension_that_is_short(self):
        """One refusal, all the reasons — not one retry per dimension."""
        services.reserve(owner=self.user, name="most", limits=Limits(
            cpu_millicores=3800, ram_mb=8000, scratch_mb=4000, pids=1000))
        with self.assertRaises(ValidationError) as caught:
            services.reserve(owner=self.other, name="rest", limits=HALF)
        message = str(caught.exception)
        self.assertIn("mCPU", message)
        self.assertIn("MB RAM", message)
        self.assertIn("PIDs", message)

    def test_releasing_returns_the_capacity(self):
        lease = services.reserve(owner=self.user, name="temp", limits=HALF)
        services.release(lease=lease, reason="done")
        self.assertTrue(HALF.fits_in(services.available()))
        self.assertEqual(services.available().cpu_millicores,
                         POOL["cpu_millicores"])

    def test_release_is_idempotent_and_keeps_the_first_reason(self):
        lease = services.reserve(owner=self.user, name="temp", limits=SMALL)
        services.release(lease=lease, reason="first")
        first_time = lease.released_at
        services.release(lease=lease, reason="second")
        lease.refresh_from_db()
        self.assertEqual(lease.released_at, first_time)
        self.assertEqual(lease.release_reason, "first")

    def test_an_expired_lease_stops_counting_without_being_touched(self):
        """Expiry is READ, not swept — the vault FileLock rule.

        A host whose beat is down must not slowly leak its whole pool to
        reservations nobody swept.
        """
        lease = services.reserve(owner=self.user, name="old", limits=HALF)
        ComputeLease.objects.filter(pk=lease.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(services.available().cpu_millicores,
                         POOL["cpu_millicores"])
        self.assertTrue(services.booked().is_zero)

    def test_expire_due_closes_the_rows_as_well(self):
        lease = services.reserve(owner=self.user, name="old", limits=SMALL)
        ComputeLease.objects.filter(pk=lease.pk).update(
            expires_at=timezone.now() - datetime.timedelta(seconds=1))
        self.assertEqual(services.expire_due(), 1)
        lease.refresh_from_db()
        self.assertIsNotNone(lease.released_at)
        self.assertEqual(services.expire_due(), 0)   # idempotent

    def test_a_gear_below_the_floor_is_refused(self):
        with self.assertRaises(ValidationError) as caught:
            services.reserve(owner=self.user, name="crumb",
                             limits=Limits(1, 1, 1, 1))
        self.assertIn("at least", str(caught.exception))

    def test_an_unconfigured_pool_refuses_rather_than_being_unbounded(self):
        with override_settings(ANASTASIA_POOL={}):
            with self.assertRaises(ValidationError) as caught:
                services.reserve(owner=self.user, name="x", limits=SMALL)
        self.assertEqual(caught.exception.refusal_code,
                         services.POOL_UNCONFIGURED)

    def test_the_per_user_gear_count_is_capped(self):
        with override_settings(ANASTASIA_MAX_GEARS_PER_USER=2):
            services.reserve(owner=self.user, name="a", limits=SMALL)
            services.reserve(owner=self.user, name="b", limits=SMALL)
            with self.assertRaises(ValidationError) as caught:
                services.reserve(owner=self.user, name="c", limits=SMALL)
        self.assertEqual(caught.exception.refusal_code, services.TOO_MANY_GEARS)
        # …and the cap is per user, not global.
        with override_settings(ANASTASIA_MAX_GEARS_PER_USER=2):
            services.reserve(owner=self.other, name="a", limits=SMALL)

    def test_two_live_gears_cannot_share_a_name_but_released_ones_can(self):
        first = services.reserve(owner=self.user, name="thesis", limits=SMALL)
        with self.assertRaises(ValidationError):
            services.reserve(owner=self.user, name="thesis", limits=SMALL)
        services.release(lease=first)
        services.reserve(owner=self.user, name="thesis", limits=SMALL)

    def test_reserving_writes_history(self):
        lease = services.reserve(owner=self.user, name="thesis", limits=SMALL)
        event = lease.events.get(kind=GearEvent.RESERVE)
        self.assertTrue(event.accepted)
        self.assertEqual(event.detail["ram_mb"], SMALL.ram_mb)

    def test_the_history_is_append_only(self):
        lease = services.reserve(owner=self.user, name="thesis", limits=SMALL)
        event = lease.events.first()
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()


class PoolReportTests(AnastasiaTestCase):
    def test_the_report_adds_up(self):
        services.reserve(owner=self.user, name="a", limits=SMALL)
        report = services.pool_report()
        self.assertTrue(report["configured"])
        self.assertEqual(report["gears_open"], 1)
        for field in ("cpu_millicores", "ram_mb", "scratch_mb", "pids"):
            self.assertEqual(
                report["booked"][field] + report["available"][field],
                report["total"][field])

    def test_an_over_booked_pool_reads_as_nothing_free_not_as_a_negative(self):
        """An operator who edits the pool DOWN below what is already booked
        must get "nothing free", not "minus three cores"."""
        services.reserve(owner=self.user, name="a", limits=HALF)
        with override_settings(ANASTASIA_POOL={
                "cpu_millicores": 100, "ram_mb": 128,
                "scratch_mb": 64, "pids": 32}):
            free = services.available()
        self.assertTrue(free.is_zero)
