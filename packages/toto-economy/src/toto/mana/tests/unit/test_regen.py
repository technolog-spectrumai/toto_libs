"""The refill: full on arrival, +4 an hour toward the cap, never twice."""

from datetime import timedelta
from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.assets.models import LedgerTransaction
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.services.assets import distribute_asset, transfer_asset
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.services.faucets import period_label
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.mana import services
from toto.mana.models import ManaGrant

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
User = get_user_model()
_ref = count()


@override_settings(**MASTER)
class RegenTestCase(TestCase):
    #: A fixed hour, so "the same hour" and "the next hour" are exact.
    HOUR = timezone.now().replace(minute=30, second=0, microsecond=0)

    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.pools = services.pools()
        self.ada = User.objects.create_user("ada", password="pw")

    def held(self, user, role):
        pool = self.pools[role]
        return Decimal(services.balance_base_units(user, pool)) / 10 ** pool.asset.decimals

    def spend(self, user, role, amount):
        """An ordinary transfer out of the pool — what a charge does."""
        pool = self.pools[role]
        account, _ = get_or_create_prepaid_account(user)
        transfer_asset(asset=pool.asset, sender_account=account,
                       receiver_account=pool.asset.reserve_account,
                       amount=Decimal(amount), reference=f"test-spend-{next(_ref)}")


class ArrivalTests(RegenTestCase):
    def test_the_signup_hook_is_held_strongly(self):
        """A local function connected weakly is collected when ready() returns.
        DEBUG=True hid it (Django's argument check caches the receiver), so it
        is asserted on the connection: no deployed member started full."""
        import weakref

        from django.db.models.signals import post_save

        hooks = {entry[0][0]: entry[1] for entry in post_save.receivers}
        hook = hooks.get("mana_fill_pools_on_user_create")
        self.assertIsNotNone(hook, "signup is not hooked at all")
        self.assertNotIsInstance(hook, weakref.ReferenceType)

    def test_a_new_member_starts_with_three_full_pools(self):
        for role in ("security", "compute", "storage"):
            with self.subTest(role=role):
                self.assertEqual(self.held(self.ada, role), Decimal("100"))

    def test_the_opening_fill_is_claimed_once(self):
        self.assertEqual(ManaGrant.objects.filter(user=self.ada, key="signup").count(), 3)
        self.assertEqual(services.fill_pools(self.ada), 0)

    def test_a_second_fill_under_one_reason_moves_nothing(self):
        """The ledger's own reference is the second layer: even with the claim
        rows gone, the same ``signup`` transfer is not paid twice."""
        ManaGrant.objects.all().delete()
        self.spend(self.ada, "compute", "100")
        services.fill_pools(self.ada)
        self.assertEqual(self.held(self.ada, "compute"), Decimal("0"))


class HourTests(RegenTestCase):
    def test_an_hour_adds_the_regen_rate(self):
        self.spend(self.ada, "storage", "10")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(self.held(self.ada, "storage"), Decimal("94"))

    def test_it_never_fills_past_the_cap(self):
        self.spend(self.ada, "storage", "2")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(self.held(self.ada, "storage"), Decimal("100"))

    def test_the_same_hour_twice_pays_once(self):
        self.spend(self.ada, "compute", "10")
        services.regenerate_hour(at=self.HOUR)
        second = services.regenerate_hour(at=self.HOUR)
        self.assertEqual(self.held(self.ada, "compute"), Decimal("94"))
        self.assertGreaterEqual(second.skipped, 1)

    def test_a_spend_between_two_runs_of_one_hour_is_not_refilled(self):
        self.spend(self.ada, "compute", "10")
        services.regenerate_hour(at=self.HOUR)
        self.spend(self.ada, "compute", "10")
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(self.held(self.ada, "compute"), Decimal("84"))

    def test_the_next_hour_pays_again(self):
        self.spend(self.ada, "security", "10")
        services.regenerate_hour(at=self.HOUR)
        services.regenerate_hour(at=self.HOUR + timedelta(hours=1))
        self.assertEqual(self.held(self.ada, "security"), Decimal("98"))

    def test_a_full_member_costs_no_claim_row(self):
        report = services.regenerate_hour(at=self.HOUR)
        self.assertEqual(report.full, 3)
        self.assertFalse(ManaGrant.objects.filter(key__startswith="hourly:").exists())

    def test_the_reference_names_the_pool_the_member_and_the_hour(self):
        self.spend(self.ada, "storage", "10")
        services.regenerate_hour(at=self.HOUR)
        ref = f"mana:storage:{self.ada.pk}:{period_label(self.HOUR)}"
        self.assertTrue(LedgerTransaction.objects.filter(reference=ref).exists())

    def test_an_inactive_member_is_not_refilled(self):
        self.spend(self.ada, "storage", "10")
        self.ada.is_active = False
        self.ada.save(update_fields=["is_active"])
        services.regenerate_hour(at=self.HOUR)
        self.assertEqual(self.held(self.ada, "storage"), Decimal("90"))


class FailureTests(RegenTestCase):
    def test_one_dry_pool_does_not_stop_the_others(self):
        for role in self.pools:
            self.spend(self.ada, role, "10")
        green = self.pools["storage"].asset
        green.reserve_account = None
        green.save(update_fields=["reserve_account"])
        self.pools = services.pools()
        report = services.regenerate_hour(at=self.HOUR)
        self.assertEqual(report.paid, 2)
        self.assertEqual(self.held(self.ada, "compute"), Decimal("94"))
        self.assertEqual(self.held(self.ada, "storage"), Decimal("90"))

    def test_a_failed_transfer_is_recorded_on_its_claim(self):
        self.spend(self.ada, "storage", "10")
        pool = self.pools["storage"]
        with self.settings():
            from unittest import mock
            with mock.patch("toto.assets.services.assets.distribute_asset",
                            side_effect=RuntimeError("ledger said no")):
                report = services.regenerate_hour(at=self.HOUR)
        self.assertEqual(report.failed, 1)
        grant = ManaGrant.objects.get(role=pool.role, key=period_label(self.HOUR))
        self.assertIn("ledger said no", grant.detail)
        self.assertIsNone(grant.transaction)

    def test_a_lost_transaction_is_reconciled_from_the_ledger(self):
        """A crash between the transfer and the claim's update."""
        self.spend(self.ada, "storage", "10")
        pool = self.pools["storage"]
        label = period_label(self.HOUR)
        account, _ = get_or_create_prepaid_account(self.ada)
        tx = distribute_asset(asset=pool.asset, recipient_account=account,
                              amount=Decimal("4"),
                              reference=services.reference_for(self.ada, "storage", label))
        ManaGrant.objects.create(user=self.ada, role="storage", key=label)
        services.regenerate_hour(at=self.HOUR)
        grant = ManaGrant.objects.get(user=self.ada, role="storage", key=label)
        self.assertEqual(grant.transaction, tx)
        self.assertEqual(self.held(self.ada, "storage"), Decimal("94"))


class TaskTests(RegenTestCase):
    def test_the_task_reports_what_it_did(self):
        from toto.mana.tasks import regenerate_hour

        self.spend(self.ada, "storage", "10")
        result = regenerate_hour()
        self.assertEqual(result["paid"], 1)
        self.assertEqual(result["failed"], 0)

    def test_the_beat_schedules_it_off_the_hour(self):
        from toto.schedules import beat_schedule

        entry = beat_schedule(mana=True)["mana-hourly-regen"]
        self.assertEqual(entry["task"], "toto.mana.tasks.regenerate_hour")
