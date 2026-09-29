"""Clearance-set refill speed, underneath the pages (2026-09-28).

``test_clearance_regen`` pins what a member sees; these pin the rules the hourly
run is built on: the pool's own rate as the off switch even against a fast
clearance, a slower or a zero clearance, the clearances read in ONE query for the
whole run, a person with no user, a functional community's stray speed, and
how the run accounts for everyone it did not pay. Plus the edges of a single
top-up and of a grant by hand, and what a pool refuses to be configured as.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase

from toto.assets.models import FaucetRun
from toto.assets.services.faucets import period_label
from toto.mana import services
from toto.mana.models import ManaGrant, ManaPool
from toto.mana.tests.fixtures import economy, held, master, spend
from toto.people.models import Person
from toto.socialhub.models import Clearance

User = get_user_model()

#: A fixed hour, so a run's label never depends on the wall clock.
HOUR = datetime(2026, 9, 29, 10, 30, tzinfo=dt_timezone.utc)


def clearance(name, speed=None, **speeds):
    fields = {f"regen_{role}": speed for role in ("security", "compute", "storage")}
    fields.update({f"regen_{role}": value for role, value in speeds.items()})
    return Clearance.objects.create(name=name, **fields)


def member(username, *clearances):
    user = User.objects.create_user(username, password="pw")
    Person.objects.create(user=user, display_name=username).clearances.add(*clearances)
    return user


@master
class RegenForTests(TestCase):
    def setUp(self):
        economy()
        self.pools = services.pools()
        self.compute = self.pools["compute"]

    def test_the_pool_off_switch_beats_a_fast_clearance(self):
        ada = member("ada", clearance("confidential", Decimal("50")))
        self.compute.regen_per_hour = Decimal("0")
        self.compute.save()
        self.assertEqual(services.regen_for(ada, self.compute), (Decimal(0), ""))

    def test_the_off_switch_asks_no_clearance(self):
        ada = member("ada", clearance("confidential", Decimal("50")))
        self.compute.regen_per_hour = Decimal("0")
        with self.assertNumQueries(0):
            services.regen_for(ada, self.compute)

    def test_a_clearance_may_set_a_slower_speed_than_the_pool(self):
        ada = member("ada", clearance("onboarding", Decimal("1.5")))
        self.assertEqual(services.regen_for(ada, self.compute), (Decimal("1.5"), "onboarding"))

    def test_a_zero_speed_clearance_is_named_with_its_zero(self):
        ada = member("ada", clearance("paused", Decimal("0")))
        self.assertEqual(services.regen_for(ada, self.compute), (Decimal("0"), "paused"))

    def test_a_faster_clearance_beats_a_zero_one(self):
        ada = member("ada", clearance("paused", Decimal("0")), clearance("confidential", Decimal("6")))
        self.assertEqual(services.regen_for(ada, self.compute), (Decimal("6"), "confidential"))

    def test_a_clearance_that_sets_another_pool_leaves_this_one_at_the_pool_rate(self):
        ada = member("ada", clearance("archive", storage=Decimal("30")))
        self.assertEqual(services.regen_for(ada, self.compute), (Decimal("4"), ""))
        self.assertEqual(services.regen_for(ada, self.pools["storage"]),
                         (Decimal("30"), "archive"))

    def test_speeds_handed_in_are_used_without_a_query(self):
        ada = User.objects.create_user("ada", password="pw")
        with self.assertNumQueries(0):
            rate = services.regen_for(ada, self.compute, {"compute": (Decimal("9"), "given")})
        self.assertEqual(rate, (Decimal("9"), "given"))

    def test_an_unsaved_person_keeps_the_pool_rate_without_a_query(self):
        with self.assertNumQueries(0):
            self.assertEqual(services.regen_for(User(username="ghost"), self.compute),
                             (Decimal("4"), ""))


@master
class ClearanceSpeedsTests(TestCase):
    def setUp(self):
        economy()
        self.confidential = clearance("confidential", Decimal("12"))
        self.payroll = clearance("payroll", Decimal("8"), storage=Decimal("20"))
        self.ada = member("ada", self.confidential, self.payroll)
        self.bob = member("bob", self.payroll)
        self.cy = member("cy")

    def test_highest_wins_pool_by_pool_naming_the_clearance(self):
        speeds = services.clearance_speeds()
        self.assertEqual(speeds[self.ada.pk], {
            "security": (Decimal("12"), "confidential"), "compute": (Decimal("12"), "confidential"),
            "storage": (Decimal("20"), "payroll")})
        self.assertEqual(speeds[self.bob.pk]["compute"], (Decimal("8"), "payroll"))

    def test_a_person_in_no_clearance_is_absent(self):
        self.assertNotIn(self.cy.pk, services.clearance_speeds())

    def test_it_is_one_query_however_many_members(self):
        for n in range(5):
            member(f"extra{n}", self.confidential, self.payroll)
        with self.assertNumQueries(1):
            speeds = services.clearance_speeds()
        self.assertEqual(len(speeds), 7)

    def test_it_can_be_narrowed_to_some_people(self):
        self.assertEqual(set(services.clearance_speeds([self.bob.pk])), {self.bob.pk})
        self.assertEqual(services.clearance_speeds([]), {})

    def test_a_clearance_that_sets_nothing_contributes_nothing(self):
        dee = member("dee", clearance("quiet"))
        self.assertNotIn(dee.pk, services.clearance_speeds())

    def test_a_person_with_no_account_is_not_a_key(self):
        ghost = Person.objects.create(display_name="ghost")
        ghost.clearances.add(self.confidential)
        self.assertNotIn(None, services.clearance_speeds())

    def test_a_host_without_socialhub_has_no_speeds(self):
        real = apps.is_installed
        with mock.patch.object(apps, "is_installed",
                               side_effect=lambda name: name != "toto.socialhub" and real(name)):
            self.assertEqual(services.clearance_speeds(), {})


@master
class RegenerateHourTests(TestCase):
    def setUp(self):
        economy()
        self.pools = services.pools()
        self.confidential = clearance("confidential", Decimal("12"))
        self.paused = clearance("paused", Decimal("0"))
        self.ada = member("ada", self.confidential)          # fast
        self.bob = member("bob", self.paused)         # stopped
        self.cy = User.objects.create_user("cy", password="pw")   # the pool's rate
        for user in (self.ada, self.bob, self.cy):
            spend(user, "compute", "50")

    def test_each_member_is_paid_their_own_speed(self):
        services.regenerate_hour(at=HOUR)
        self.assertEqual([held(u, "compute") for u in (self.ada, self.bob, self.cy)],
                         [Decimal("62"), Decimal("50"), Decimal("54")])

    def test_the_clearances_are_read_once_for_the_whole_run(self):
        with mock.patch.object(services, "clearance_speeds", wraps=services.clearance_speeds) as read:
            services.regenerate_hour(at=HOUR)
        read.assert_called_once_with()

    def test_a_zero_speed_member_is_counted_as_skipped_not_failed(self):
        report = services.regenerate_hour(at=HOUR)
        run = FaucetRun.objects.get(faucet__slug="mana-compute-hourly", period_label=period_label(HOUR))
        self.assertEqual((run.paid, run.failed), (2, 0))
        self.assertEqual(report.failed, 0)
        self.assertFalse(ManaGrant.objects.filter(user=self.bob, key=period_label(HOUR)).exists())

    def test_the_report_adds_up_across_pools(self):
        report = services.regenerate_hour(at=HOUR)
        members = User.objects.filter(is_active=True).count()
        self.assertEqual(report.paid, 2)                       # only compute was spent
        self.assertEqual(report.paid + report.full + report.skipped + report.failed,
                         3 * members)
        self.assertEqual(report.label, period_label(HOUR))
        self.assertIn("2 topped up", str(report))

    def test_a_switched_off_pool_writes_no_run_at_all(self):
        compute = self.pools["compute"]
        compute.regen_per_hour = Decimal("0")
        compute.save()
        services.regenerate_hour(at=HOUR)
        self.assertFalse(FaucetRun.objects.filter(faucet__slug="mana-compute-hourly").exists())
        self.assertTrue(FaucetRun.objects.filter(faucet__slug="mana-storage-hourly").exists())
        self.assertEqual(held(self.ada, "compute"), Decimal("50"))

    def test_a_pool_whose_asset_is_inactive_is_passed_over(self):
        asset = self.pools["compute"].asset
        type(asset).objects.filter(pk=asset.pk).update(active=False)
        services.regenerate_hour(at=HOUR)
        self.assertFalse(FaucetRun.objects.filter(faucet__slug="mana-compute-hourly").exists())
        self.assertEqual(held(self.cy, "compute"), Decimal("50"))

    def test_a_failure_is_counted_and_written_on_the_run(self):
        real = services.top_up

        def flaky(user, pool, **kwargs):
            if user == self.cy and pool.role == "compute":
                return "cy: reserve dry"
            return real(user, pool, **kwargs)

        with mock.patch.object(services, "top_up", side_effect=flaky):
            report = services.regenerate_hour(at=HOUR)
        self.assertEqual(report.failed, 1)
        self.assertEqual(report.failures, ["compute: cy: reserve dry"])
        run = FaucetRun.objects.get(faucet__slug="mana-compute-hourly")
        self.assertEqual(run.failed, 1)
        self.assertIn("cy: reserve dry", run.detail)
        self.assertIsNotNone(run.finished_at)

    def test_an_inactive_member_is_not_even_asked(self):
        self.cy.is_active = False
        self.cy.save()
        with mock.patch.object(services, "top_up", wraps=services.top_up) as topped:
            services.regenerate_hour(at=HOUR)
        self.assertNotIn(self.cy, [c.args[0] for c in topped.call_args_list])


@master
class TopUpEdgeTests(TestCase):
    def setUp(self):
        economy()
        self.pool = services.pools()["compute"]
        self.ada = User.objects.create_user("ada", password="pw")

    def test_a_full_member_is_not_claimed(self):
        self.assertEqual(services.top_up(self.ada, self.pool, key="k1", cap=Decimal("4")), "full")
        self.assertFalse(ManaGrant.objects.filter(key="k1").exists())

    def test_no_cap_fills_to_the_maximum_in_one_grant(self):
        spend(self.ada, "compute", "73")
        self.assertEqual(services.top_up(self.ada, self.pool, key="k2"), "paid")
        self.assertEqual(held(self.ada, "compute"), Decimal("100"))
        grant = ManaGrant.objects.get(key="k2")
        self.assertEqual(grant.amount_base_units, 73 * 10 ** self.pool.asset.decimals)

    def test_a_claim_that_cannot_be_written_is_a_failure_not_a_raise(self):
        spend(self.ada, "compute", "10")
        with mock.patch.object(ManaGrant.objects, "create", side_effect=RuntimeError("db gone")), \
                self.assertLogs("toto.mana", level="ERROR"):
            outcome = services.top_up(self.ada, self.pool, key="k3", cap=Decimal("4"))
        self.assertIn("db gone", outcome)
        self.assertEqual(held(self.ada, "compute"), Decimal("90"))

    def test_a_key_claimed_before_is_skipped(self):
        spend(self.ada, "compute", "10")
        with mock.patch.object(ManaGrant.objects, "create", side_effect=IntegrityError("dup")):
            self.assertEqual(services.top_up(self.ada, self.pool, key="k4", cap=Decimal("4")),
                             "skipped")

    def test_a_failed_payment_keeps_its_reason_on_the_claim(self):
        spend(self.ada, "compute", "10")
        with mock.patch("toto.mana.services._pay", side_effect=RuntimeError("reserve dry")), \
                self.assertLogs("toto.mana", level="WARNING"):
            outcome = services.top_up(self.ada, self.pool, key="k5", cap=Decimal("4"))
        self.assertIn("reserve dry", outcome)
        self.assertEqual(ManaGrant.objects.get(key="k5").detail, "reserve dry")

    def test_fill_pools_never_raises(self):
        with mock.patch.object(services, "pools", side_effect=RuntimeError("boom")), \
                self.assertLogs("toto.mana", level="ERROR"):
            self.assertEqual(services.fill_pools(self.ada, reason="again"), 0)

    def test_fill_pools_skips_a_pool_that_cannot_pay_and_counts_the_rest(self):
        spend(self.ada, "compute", "10")
        spend(self.ada, "security", "10")
        asset = self.pool.asset
        type(asset).objects.filter(pk=asset.pk).update(active=False)
        self.assertEqual(services.fill_pools(self.ada, reason="refill"), 1)   # security only
        self.assertEqual((held(self.ada, "security"), held(self.ada, "compute")),
                         (Decimal("100"), Decimal("90")))


@master
class ManualGrantRefusalTests(TestCase):
    def setUp(self):
        economy()
        self.pool = services.pools()["security"]
        self.ada = User.objects.create_user("ada", password="pw")
        self.root = User.objects.create_superuser("root", password="pw")
        spend(self.ada, "security", "40")

    def grant(self, amount, reason="Prize"):
        return services.grant_manual(self.ada, self.pool, amount, reason=reason,
                                     granted_by=self.root)

    def test_an_amount_that_is_not_a_number_is_refused(self):
        with self.assertRaisesMessage(services.GrantRefused, "not a number"):
            self.grant("ten")

    def test_zero_and_negative_amounts_are_refused(self):
        for amount in ("0", "-5"):
            with self.subTest(amount=amount), \
                    self.assertRaisesMessage(services.GrantRefused, "above zero"):
                self.grant(amount)

    def test_a_pool_that_cannot_pay_is_refused_before_anything_is_claimed(self):
        asset = self.pool.asset
        type(asset).objects.filter(pk=asset.pk).update(active=False)
        self.pool = services.pools()["security"]
        with self.assertRaisesMessage(services.GrantRefused, "cannot pay"):
            self.grant("5")
        self.assertFalse(ManaGrant.objects.filter(key__startswith="manual:").exists())

    def test_the_reason_is_trimmed_onto_the_payout(self):
        self.assertEqual(self.grant("5", reason="  Contest prize  "), "paid")
        grant = ManaGrant.objects.get(key__startswith="manual:")
        self.assertEqual(grant.payout.reason, "Contest prize")
        self.assertEqual(held(self.ada, "security"), Decimal("65"))

    def test_two_grants_are_two_claims(self):
        self.grant("5")
        self.grant("5")
        self.assertEqual(ManaGrant.objects.filter(key__startswith="manual:").count(), 2)
        self.assertEqual(held(self.ada, "security"), Decimal("70"))


@master
class PoolConfigurationTests(TestCase):
    def setUp(self):
        economy()
        self.pool = services.pools()["compute"]

    def assertRefused(self, field):
        with self.assertRaises(ValidationError) as caught:
            self.pool.clean()
        self.assertIn(field, caught.exception.message_dict)

    def test_a_negative_refill_is_refused(self):
        self.pool.regen_per_hour = Decimal("-1")
        self.assertRefused("regen_per_hour")

    def test_a_zero_refill_is_the_allowed_off_switch(self):
        self.pool.regen_per_hour = Decimal("0")
        self.pool.clean()

    def test_an_empty_maximum_is_refused(self):
        self.pool.max_pool = Decimal("0")
        self.assertRefused("max_pool")

    def test_an_inactive_asset_is_refused(self):
        self.pool.asset.active = False
        self.assertRefused("asset")

    def test_an_asset_with_no_reserve_is_refused(self):
        self.pool.asset.reserve_account_id = None
        self.assertRefused("asset")

    def test_a_maximum_too_large_for_a_holding_is_refused(self):
        self.pool.max_pool = Decimal(10) ** 12                  # × 10**9 base units > bigint
        self.assertRefused("max_pool")

    def test_the_seeded_pool_is_valid(self):
        self.pool.clean()
        self.assertEqual(str(self.pool), f"compute → {self.pool.asset.unit_name}")

    def test_a_second_binding_for_a_role_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError):
            ManaPool.objects.create(role="compute", asset=self.pool.asset,
                                    regen_per_hour=Decimal("1"), max_pool=Decimal("1"))
