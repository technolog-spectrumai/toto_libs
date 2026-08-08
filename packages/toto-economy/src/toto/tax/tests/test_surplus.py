"""The community fee: models, desk, sweep math, idempotency, visibility."""

import datetime
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.assets.models import (
    AccountType, AssetHolding, LedgerAccount, LedgerTransaction,
)
from toto.assets.queries import verify_asset_ledger

from .. import services, surplus
from ..models import SurplusCharge, SurplusChargeStatus, SurplusPolicy
from .factories import (
    fund_account, make_gas_asset, make_surplus_policy, make_user,
    make_user_account,
)

ASR = 10 ** 9  # one ASR in base units at 9 decimals


def fee_account_balance(asset):
    holding = AssetHolding.objects.filter(
        account__code=surplus.COMMUNITY_FEE_ACCOUNT_CODE, asset=asset).first()
    return holding.balance_base_units if holding else 0


class ModelTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset(decimals=9)

    def test_threshold_base_units_derived_on_save(self):
        policy = make_surplus_policy(self.asset, threshold="100")
        self.assertEqual(policy.threshold_base_units, 100 * ASR)

        policy.threshold_display = Decimal("2.5")
        policy.save()
        self.assertEqual(policy.threshold_base_units, int(2.5 * ASR))

    def test_rate_bounds(self):
        policy = make_surplus_policy(self.asset, rate="0.02")
        policy.rate = Decimal("0.3")
        with self.assertRaises(ValidationError):
            policy.full_clean()


class DeskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        cls.staff = make_user("staff", is_staff=True)
        cls.alice = make_user("alice")

    def setUp(self):
        self.asset = make_gas_asset(decimals=9)

    def test_staff_only(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("tax:rules")).status_code, 403)

    def test_create_update_and_delete_policy(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("tax:rules"), {
            f"threshold__{self.asset.pk}": "100",
            f"rate_pct__{self.asset.pk}": "2",
            f"period__{self.asset.pk}": "monthly",
            f"surplus_active__{self.asset.pk}": "on",
        })
        self.assertEqual(response.status_code, 302)
        policy = SurplusPolicy.objects.get(asset=self.asset)
        self.assertEqual(policy.rate, Decimal("0.02"))  # percent stored ÷ 100
        self.assertEqual(policy.threshold_base_units, 100 * ASR)
        self.assertEqual(policy.period, "monthly")

        # Blank threshold deletes.
        self.client.post(reverse("tax:rules"), {
            f"threshold__{self.asset.pk}": "",
        })
        self.assertFalse(SurplusPolicy.objects.exists())

    def test_bad_rate_is_rejected_without_a_write(self):
        self.client.force_login(self.staff)

        response = self.client.post(reverse("tax:rules"), {
            f"threshold__{self.asset.pk}": "100",
            f"rate_pct__{self.asset.pk}": "30",
            f"period__{self.asset.pk}": "monthly",
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(SurplusPolicy.objects.exists())

    def test_both_grids_post_together(self):
        from ..models import TaxRule

        TaxRule.objects.create(metric_code="storage.gb_day",
                               allowance=Decimal("1"), unit_label="GB")
        self.client.force_login(self.staff)

        response = self.client.post(reverse("tax:rules"), {
            "allowance__storage.gb_day": "3",
            "active__storage.gb_day": "on",
            f"threshold__{self.asset.pk}": "50",
            f"rate_pct__{self.asset.pk}": "1",
            f"period__{self.asset.pk}": "weekly",
            f"surplus_active__{self.asset.pk}": "on",
        })

        self.assertEqual(response.status_code, 302)
        self.assertEqual(TaxRule.objects.get(metric_code="storage.gb_day").allowance,
                         Decimal("3"))
        self.assertEqual(SurplusPolicy.objects.get(asset=self.asset).period, "weekly")


class SweepTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset(decimals=9)
        self.policy = make_surplus_policy(self.asset, threshold="100",
                                          rate="0.02", period="monthly")
        self.alice = make_user("alice")

    def test_under_threshold_untouched(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 90 * ASR)

        summary = surplus.run_surplus_sweep()

        self.assertEqual(summary["ASR"]["charged"], 0)
        self.assertEqual(SurplusCharge.objects.count(), 0)
        self.assertEqual(fee_account_balance(self.asset), 0)

    def test_fee_math_and_destination(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)

        summary = surplus.run_surplus_sweep()

        # floor(50 ASR × 0.02) = 1 ASR.
        self.assertEqual(summary["ASR"]["charged"], 1)
        charge = SurplusCharge.objects.get()
        self.assertEqual(charge.fee_base, 1 * ASR)
        self.assertEqual(charge.status, SurplusChargeStatus.COLLECTED)
        self.assertEqual(fee_account_balance(self.asset), 1 * ASR)
        account_holding = AssetHolding.objects.get(account=account)
        self.assertEqual(account_holding.balance_base_units, 149 * ASR)
        self.assertTrue(charge.period_label.startswith("monthly:"))

    def test_floor_keeps_the_dust(self):
        # Surplus of 30 base units at 2% → floor(0.6) = 0.
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 100 * ASR + 30)

        summary = surplus.run_surplus_sweep()

        self.assertEqual(summary["ASR"]["skipped_zero"], 1)
        self.assertEqual(SurplusCharge.objects.count(), 0)

    def test_multi_account_aggregation_and_largest_first(self):
        big = make_user_account(self.alice, "big")
        small = make_user_account(self.alice, "small")
        fund_account(big, self.asset, 80 * ASR)
        fund_account(small, self.asset, 70 * ASR)  # each under, total 150 over

        surplus.run_surplus_sweep()

        charge = SurplusCharge.objects.get()
        self.assertEqual(charge.fee_base, 1 * ASR)
        # Largest-first: the whole fee fits in "big".
        self.assertEqual(charge.allocation, [[big.pk, 1 * ASR]])
        self.assertEqual(
            AssetHolding.objects.get(account=big).balance_base_units, 79 * ASR)

    def test_exemptions(self):
        system = LedgerAccount.objects.create(
            code="sys", name="sys", account_type=AccountType.SYSTEM, active=True)
        external = LedgerAccount.objects.create(
            code="ext", name="ext", account_type=AccountType.EXTERNAL,
            user=None, active=True)
        fund_account(system, self.asset, 1000 * ASR)
        fund_account(external, self.asset, 1000 * ASR)

        summary = surplus.run_surplus_sweep()

        self.assertEqual(SurplusCharge.objects.count(), 0)
        self.assertEqual(summary["ASR"]["charged"], 0)

    def test_inactive_policy_skipped(self):
        self.policy.active = False
        self.policy.save()
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 500 * ASR)

        summary = surplus.run_surplus_sweep()

        self.assertEqual(summary, {})
        self.assertEqual(SurplusCharge.objects.count(), 0)

    def test_same_period_idempotent_next_period_charges_again(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)

        surplus.run_surplus_sweep()
        surplus.run_surplus_sweep()

        self.assertEqual(SurplusCharge.objects.count(), 1)
        self.assertEqual(LedgerTransaction.objects.filter(
            reference__startswith="tax-surplus-").count(), 1)

        next_month = timezone.now() + datetime.timedelta(days=40)
        surplus.run_surplus_sweep(now=next_month)

        self.assertEqual(SurplusCharge.objects.count(), 2)

    def test_crash_recovery_replays_the_stored_allocation(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)
        label = surplus.period_label_for(self.policy)
        charge = SurplusCharge.objects.create(
            policy=self.policy, user=self.alice, period_label=label,
            total_base=150 * ASR, threshold_base=100 * ASR,
            fee_base=1 * ASR, rate=self.policy.rate,
            allocation=[[account.pk, 1 * ASR]],
        )

        summary = surplus.run_surplus_sweep()

        charge.refresh_from_db()
        self.assertEqual(charge.status, SurplusChargeStatus.COLLECTED)
        self.assertEqual(summary["ASR"]["replayed"], 1)
        self.assertEqual(fee_account_balance(self.asset), 1 * ASR)

        surplus.run_surplus_sweep()  # replayed transfers dedupe by reference
        self.assertEqual(fee_account_balance(self.asset), 1 * ASR)

    def test_transfer_failure_stays_pending_then_collects(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)

        with patch("toto.assets.services.assets.transfer_asset",
                   side_effect=ValidationError("frozen")):
            summary = surplus.run_surplus_sweep()

        charge = SurplusCharge.objects.get()
        self.assertEqual(charge.status, SurplusChargeStatus.PENDING)
        self.assertEqual(summary["ASR"]["pending"], 1)

        summary = surplus.run_surplus_sweep()
        charge.refresh_from_db()
        self.assertEqual(charge.status, SurplusChargeStatus.COLLECTED)
        self.assertEqual(summary["ASR"]["replayed"], 1)

    def test_surplus_gone_before_collection_is_skipped(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)
        label = surplus.period_label_for(self.policy)
        charge = SurplusCharge.objects.create(
            policy=self.policy, user=self.alice, period_label=label,
            total_base=150 * ASR, threshold_base=100 * ASR,
            fee_base=1 * ASR, rate=self.policy.rate,
            allocation=[[account.pk, 1 * ASR]],
        )
        fund_account(account, self.asset, 50 * ASR)  # spent below threshold

        surplus.run_surplus_sweep()

        charge.refresh_from_db()
        self.assertEqual(charge.status, SurplusChargeStatus.SKIPPED)
        self.assertEqual(fee_account_balance(self.asset), 0)

    def test_shrunken_surplus_recomputes_before_anything_posted(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)
        label = surplus.period_label_for(self.policy)
        SurplusCharge.objects.create(
            policy=self.policy, user=self.alice, period_label=label,
            total_base=150 * ASR, threshold_base=100 * ASR,
            fee_base=1 * ASR, rate=self.policy.rate,
            allocation=[[account.pk, 1 * ASR]],
        )
        # The user spent down to 100.5 ASR: cap = 0.5 ASR < the 1 ASR fee.
        fund_account(account, self.asset, 100 * ASR + ASR // 2)

        surplus.run_surplus_sweep()

        charge = SurplusCharge.objects.get()
        self.assertEqual(charge.status, SurplusChargeStatus.COLLECTED)
        self.assertEqual(charge.fee_base, ASR // 2)  # shrunk, never over
        self.assertEqual(fee_account_balance(self.asset), ASR // 2)

    def test_ledger_stays_balanced(self):
        account = make_user_account(self.alice, "a1")
        fund_account(account, self.asset, 150 * ASR)

        surplus.run_surplus_sweep()

        self.assertTrue(verify_asset_ledger(self.asset)["entries_balanced"])

    def test_levy_run_survives_a_sweep_failure(self):
        with patch.object(surplus, "run_surplus_sweep",
                          side_effect=RuntimeError("boom")):
            summaries = services.run_daily_levy()
        self.assertIsInstance(summaries, list)


class VisibilityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "test", "publication_year": 2026, "active": True},
        )
        cls.alice = make_user("alice")

    def setUp(self):
        self.asset = make_gas_asset(decimals=9)
        self.policy = make_surplus_policy(self.asset, threshold="100",
                                          rate="0.02", period="monthly")
        self.account = make_user_account(self.alice, "a1")
        fund_account(self.account, self.asset, 150 * ASR)

    def test_estimate_and_wallet_fee_map(self):
        rows = surplus.estimate_for_user(self.alice)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rate_pct"], "2")
        self.assertEqual(rows[0]["surplus"], Decimal("50"))
        self.assertEqual(rows[0]["estimate"], Decimal("1"))

        fee_map = surplus.wallet_fee_map(self.alice)
        self.assertIn(self.asset.pk, fee_map)

    def test_my_levies_renders_the_community_section(self):
        self.client.force_login(self.alice)
        content = self.client.get(reverse("tax:my_levies")).content.decode()
        self.assertIn("Community fee", content)
        self.assertIn("prevents idle accounts", content)

    def test_wallet_renders_the_fee_line(self):
        self.client.force_login(self.alice)
        content = self.client.get(reverse("assets:wallet")).content.decode()
        self.assertIn("Community fee", content)

    def test_asset_detail_plugin_present_only_with_a_policy(self):
        url = reverse("assets:asset_detail", args=[self.asset.pk])
        content = self.client.get(url).content.decode()
        self.assertIn("Community fee", content)
        self.assertIn("prevents idle accounts", content)

        self.policy.active = False
        self.policy.save()
        content = self.client.get(url).content.decode()
        self.assertNotIn("Community fee", content)