"""The heart: the daily levy against the real vault + tariffs + assets stack."""

import datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from toto.assets.models import AssetHolding, to_base_units
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.tariffs.models import UsageRecord, UsageStatus
from toto.vault.models import VaultUsageEvent

from .. import services
from ..models import ArrearsStatus, TaxArrearsCase
from .factories import (
    GB, fund_prepaid, make_gas_asset, make_rule, make_user, make_vault_file,
    price_gb_day,
)


class LevyTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset(decimals=9)
        self.rule = make_rule(allowance="1")

    # -- nothing billable ---------------------------------------------------

    def test_under_allowance_writes_nothing(self):
        user = make_user("alice")
        make_vault_file(user, GB // 2)
        price_gb_day("0.5")

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.UNDER_ALLOWANCE: 1})
        self.assertEqual(VaultUsageEvent.objects.count(), 0)
        self.assertEqual(UsageRecord.objects.count(), 0)

    def test_at_allowance_exactly_is_free(self):
        user = make_user("alice")
        make_vault_file(user, GB)
        price_gb_day("0.5")

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.UNDER_ALLOWANCE: 1})
        self.assertEqual(VaultUsageEvent.objects.count(), 0)

    def test_under_allowance_resolves_an_open_case(self):
        user = make_user("alice")
        make_vault_file(user, GB // 2)
        case = TaxArrearsCase.objects.create(user=user, rule=self.rule)

        services.levy_rule(self.rule)

        case.refresh_from_db()
        self.assertEqual(case.status, ArrearsStatus.RESOLVED)
        self.assertEqual(case.resolution_reason, "under_allowance")

    # -- the priced happy path ----------------------------------------------

    def test_levies_the_excess_and_posts_the_charge(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)
        price_gb_day("0.5")
        # 2 GB over allowance × 0.5 ASR = 1 ASR = 1e9 base units.
        expected = 2 * to_base_units(Decimal("0.5"), self.asset.decimals)
        fund_prepaid(user, self.asset, 2 * expected)

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.LEVIED: 1})
        event = VaultUsageEvent.objects.get()
        self.assertEqual(event.metric_code, "storage.gb_day")
        self.assertEqual(event.quantity, Decimal("2"))
        self.assertEqual(event.unit, "gb_day")
        self.assertEqual(
            event.idempotency_key,
            f"tax.storage.gb_day:{user.pk}:{timezone.localdate().isoformat()}",
        )
        record = UsageRecord.objects.get()
        self.assertEqual(record.status, UsageStatus.POSTED)
        payer, _ = get_or_create_prepaid_account(user)
        holding = AssetHolding.objects.get(account=payer, asset=self.asset)
        self.assertEqual(holding.balance_base_units, expected)

    def test_same_day_is_idempotent_next_day_charges_again(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)
        price_gb_day("0.5")
        fund_prepaid(user, self.asset, 10 ** 12)

        today = timezone.localdate()
        services.levy_rule(self.rule, day=today)
        summary = services.levy_rule(self.rule, day=today)

        self.assertEqual(summary.counts, {services.Outcome.ALREADY: 1})
        self.assertEqual(VaultUsageEvent.objects.count(), 1)
        self.assertEqual(UsageRecord.objects.count(), 1)

        services.levy_rule(self.rule, day=today + datetime.timedelta(days=1))
        self.assertEqual(VaultUsageEvent.objects.count(), 2)
        self.assertEqual(UsageRecord.objects.count(), 2)

    # -- unpriced -----------------------------------------------------------

    def test_unpriced_metric_measures_but_charges_nothing(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.FREE: 1})
        self.assertEqual(VaultUsageEvent.objects.count(), 1)
        self.assertEqual(UsageRecord.objects.count(), 0)

    # -- failure ------------------------------------------------------------

    def test_insufficient_funds_opens_case_and_spares_the_solvent(self):
        poor = make_user("poor")
        rich = make_user("rich")
        make_vault_file(poor, 3 * GB)
        make_vault_file(rich, 3 * GB)
        price_gb_day("0.5")
        fund_prepaid(poor, self.asset, 1)  # a token, far short
        fund_prepaid(rich, self.asset, 10 ** 12)

        summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts,
                         {services.Outcome.FAILED: 1, services.Outcome.LEVIED: 1})
        # The failed attempt is the written-off day's audit trail.
        failed = UsageRecord.objects.filter(status=UsageStatus.FAILED)
        self.assertEqual(failed.count(), 1)
        case = TaxArrearsCase.objects.get(user=poor)
        self.assertEqual(case.rule, self.rule)
        self.assertEqual(case.failed_days, 1)
        self.assertGreater(case.last_shortfall_display, 0)
        self.assertEqual(case.last_shortfall_asset, self.asset.unit_name)
        # No Person profile in this test: warned anyway, without an event.
        self.assertEqual(case.status, ArrearsStatus.WARNED)
        self.assertEqual(case.warning_channel, "none")
        self.assertIsNotNone(case.deadline_at)
        # The rich user's charge went through untouched.
        self.assertFalse(TaxArrearsCase.objects.filter(user=rich).exists())

    def test_successful_charge_resolves_the_case(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)
        price_gb_day("0.5")
        case = TaxArrearsCase.objects.create(user=user, rule=self.rule)
        fund_prepaid(user, self.asset, 10 ** 12)

        services.levy_rule(self.rule)

        case.refresh_from_db()
        self.assertEqual(case.status, ArrearsStatus.RESOLVED)
        self.assertEqual(case.resolution_reason, "paid")

    # -- the run wrapper ----------------------------------------------------

    def test_inactive_rule_is_skipped(self):
        self.rule.active = False
        self.rule.save()
        user = make_user("alice")
        make_vault_file(user, 3 * GB)

        summaries = services.run_daily_levy()

        self.assertEqual(summaries, [])
        self.assertEqual(VaultUsageEvent.objects.count(), 0)

    def test_rule_without_provider_is_skipped_cleanly(self):
        rule = make_rule(allowance="0", metric_code="nothing.registered")

        summary = services.levy_rule(rule)

        self.assertIn("no registered metric", summary.skipped_reason)

    def test_run_daily_levy_returns_a_summary_per_rule(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)

        summaries = services.run_daily_levy()

        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].metric_code, "storage.gb_day")

    # -- the UI feed --------------------------------------------------------

    def test_estimate_for_user(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)
        price_gb_day("0.5")

        rows = services.estimate_for_user(user)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["measured_raw"], 3 * GB)
        self.assertEqual(row["measured"], Decimal("3"))
        self.assertEqual(row["billable"], Decimal("2"))
        self.assertEqual(row["estimate"]["asset"], "ASR")
        self.assertEqual(row["estimate"]["amount"], Decimal("1.000000000"))
        self.assertIsNone(row["case"])

    def test_estimate_without_price_has_no_amount(self):
        user = make_user("alice")
        make_vault_file(user, 3 * GB)

        rows = services.estimate_for_user(user)

        self.assertIsNone(rows[0]["estimate"])
