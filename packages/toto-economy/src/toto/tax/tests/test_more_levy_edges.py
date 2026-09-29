"""The nightly levy's edges: what it skips, what it survives, and what the
beat task hands back. Everything here runs against the real vault + tariffs +
assets stack (tests/factories.py); the only things patched are the collaborator
seams a test cannot otherwise reach — a provider's sample, a race in the
charge, a lookup failing.
"""

from decimal import Decimal
from unittest import mock

from django.utils import timezone

from toto.assets.models import AssetHolding, to_base_units
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.testing import LedgerTestCase as TestCase
from toto.quota.levy import registry as levy_registry
from toto.tariffs.charge import InsufficientBalanceError
from toto.tariffs.models import UsageRecord
from toto.vault.models import VaultUsageEvent

from .. import services, tasks
from ..models import ArrearsStatus, TaxArrearsCase, TaxRule
from .factories import (GB, fund_prepaid, make_gas_asset, make_rule, make_user,
                        make_vault_file, price_gb_day)


#: The storage metric's own unit — what the levy passes to every charge.
UNIT = "gb_day"


def _balance(user, asset):
    account, _ = get_or_create_prepaid_account(user)
    holding = AssetHolding.objects.filter(account=account, asset=asset).first()
    return holding.balance_base_units if holding else 0


class SkippedRuleTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset(decimals=9)

    def test_a_metric_whose_app_keeps_no_usage_table_is_never_billed(self):
        rule = make_rule()
        user = make_user("alice")
        make_vault_file(user, 3 * GB)
        price_gb_day("0.5")
        fund_prepaid(user, self.asset, 10 ** 12)

        with mock.patch.object(services, "policy_model_for", return_value=None):
            summary = services.levy_rule(rule)

        self.assertIn("no usage-event table", summary.skipped_reason)
        self.assertEqual(summary.counts, {})
        self.assertEqual(UsageRecord.objects.count(), 0)
        self.assertEqual(_balance(user, self.asset), 10 ** 12)

    def test_an_unregistered_metric_does_not_stop_the_other_rules(self):
        make_rule(metric_code="nothing.registered")
        make_rule()
        user = make_user("alice")
        make_vault_file(user, GB)

        summaries = {s.metric_code: s for s in services.run_daily_levy()}

        self.assertIn("no registered metric",
                      summaries["nothing.registered"].skipped_reason)
        self.assertEqual(summaries["storage.gb_day"].counts,
                         {services.Outcome.FREE: 1})

    def test_the_summary_is_dated_with_the_day_it_levied(self):
        import datetime

        rule = make_rule()
        summary = services.levy_rule(rule, day=datetime.date(2026, 2, 3))
        self.assertEqual(summary.day, "2026-02-03")


class ResilienceTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset(decimals=9)
        self.rule = make_rule()
        price_gb_day("0.5")
        self.alice = make_user("alice")
        self.bob = make_user("bob")
        for user in (self.alice, self.bob):
            make_vault_file(user, 2 * GB)
            fund_prepaid(user, self.asset, to_base_units(Decimal("10"), 9))

    def test_one_users_crash_is_an_error_and_everyone_else_is_still_levied(self):
        real = services.levy_user

        def flaky(rule, metric, provider, user, *args, **kwargs):
            if user.pk == self.alice.pk:
                raise RuntimeError("one bad row")
            return real(rule, metric, provider, user, *args, **kwargs)

        with mock.patch.object(services, "levy_user", side_effect=flaky):
            summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.ERROR: 1,
                                          services.Outcome.LEVIED: 1})
        self.assertEqual(_balance(self.alice, self.asset),
                         to_base_units(Decimal("10"), 9))
        self.assertEqual(_balance(self.bob, self.asset),
                         to_base_units(Decimal("9"), 9))

    def test_a_sampled_holder_with_no_account_row_is_skipped(self):
        provider = levy_registry.get("storage.gb_day")
        with mock.patch.object(provider, "sample",
                               return_value=[(987654, GB), (self.bob.pk, GB)]):
            summary = services.levy_rule(self.rule)
        self.assertEqual(summary.counts, {services.Outcome.LEVIED: 1})
        self.assertEqual(VaultUsageEvent.objects.count(), 1)

    def test_a_price_nobody_can_resolve_for_the_user_is_free_and_owes_nothing(self):
        case = TaxArrearsCase.objects.create(user=self.alice, rule=self.rule,
                                             status=ArrearsStatus.WARNED)
        with mock.patch.object(services, "price_for", return_value=None):
            summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.FREE: 2})
        self.assertEqual(UsageRecord.objects.count(), 0)
        case.refresh_from_db()
        self.assertEqual((case.status, case.resolution_reason),
                         (ArrearsStatus.RESOLVED, "unpriced"))

    def test_the_levy_event_records_what_was_measured(self):
        services.levy_rule(self.rule)
        event = VaultUsageEvent.objects.get(user=self.alice)
        self.assertEqual(event.metadata["measured_raw"], 2 * GB)
        self.assertEqual(event.idempotency_key,
                         services.idempotency_key(self.rule, self.alice.pk,
                                                  timezone.localdate()))
        self.assertNotIn("concentration_extra", event.metadata)


class ClampRaceTests(TestCase):
    """A clamped levy never turns into a debt — not even when a spend races
    in between the affordability check and the charge."""

    def setUp(self):
        self.asset = make_gas_asset(decimals=9)
        self.rule = make_rule()
        self.rule.clamp_to_balance = True
        self.rule.save(update_fields=["clamp_to_balance"])
        price_gb_day("1")
        self.user = make_user("alice")
        make_vault_file(self.user, GB)
        fund_prepaid(self.user, self.asset, to_base_units(Decimal("5"), 9))

    def test_a_charge_that_loses_the_race_is_clamped_to_nothing(self):
        TaxArrearsCase.objects.create(user=self.user, rule=self.rule,
                                      status=ArrearsStatus.OPEN)
        raced = InsufficientBalanceError("ASR", 10 ** 9, 0, asset_decimals=9)
        with mock.patch.object(services, "charge", side_effect=raced):
            summary = services.levy_rule(self.rule)

        self.assertEqual(summary.counts, {services.Outcome.CLAMPED: 1})
        self.assertFalse(TaxArrearsCase.objects.filter(
            user=self.user,
            status__in=[ArrearsStatus.OPEN, ArrearsStatus.WARNED]).exists())
        self.assertEqual(TaxArrearsCase.objects.get().resolution_reason, "clamped")

    def test_a_balance_that_never_covers_even_a_rounded_share_charges_nothing(self):
        broke = InsufficientBalanceError("ASR", 10 ** 9, 10, asset_decimals=9)
        with mock.patch.object(services, "check_funds", side_effect=broke):
            quantity = services._affordable_quantity(
                self.user, object(), "storage.gb_day", Decimal("1"), "GB")
        self.assertEqual(quantity, Decimal(0))

    def _tariff(self):
        from toto.quota.charge import price_for

        return price_for(self.user, "vault")

    def test_the_affordable_share_is_scaled_down_to_what_is_held(self):
        quantity = services._affordable_quantity(
            self.user, self._tariff(), "storage.gb_day", Decimal("7"), UNIT)
        # 5 held of 7 needed at 1 a unit: exactly five units are affordable.
        self.assertEqual(quantity, Decimal("5"))

    def test_an_affordable_quantity_is_charged_whole(self):
        quantity = services._affordable_quantity(
            self.user, self._tariff(), "storage.gb_day", Decimal("3"), UNIT)
        self.assertEqual(quantity, Decimal("3"))

    def test_no_shortfall_is_reported_when_the_user_can_pay(self):
        self.assertEqual(services._shortfall_info(
            self.user, self._tariff(), "storage.gb_day", Decimal("1"), UNIT),
            (None, ""))

    def test_a_real_shortfall_is_reported_in_display_units(self):
        shortfall, asset = services._shortfall_info(
            self.user, self._tariff(), "storage.gb_day", Decimal("8"), UNIT)
        self.assertEqual((shortfall, asset), (Decimal("3"), "ASR"))


class ConcentrationWithoutGasTests(TestCase):
    def test_with_no_billing_asset_the_term_is_off_rather_than_an_error(self):
        rule = TaxRule.objects.create(metric_code="storage.gb_day",
                                      unit_label="GB",
                                      concentration_k=Decimal("100"))
        user = make_user("alice")
        self.assertEqual(services._concentration_shares(rule, [user.pk]), {})


class EstimateEdgeTests(TestCase):
    def test_a_rule_nothing_can_measure_is_left_off_the_estimate(self):
        make_gas_asset()
        make_rule(metric_code="nothing.registered")
        make_rule()
        user = make_user("alice")
        make_vault_file(user, GB)

        rows = services.estimate_for_user(user)

        self.assertEqual([row["rule"].metric_code for row in rows],
                         ["storage.gb_day"])

    def test_the_estimate_shows_the_open_case(self):
        make_gas_asset()
        rule = make_rule()
        user = make_user("alice")
        make_vault_file(user, GB)
        case = TaxArrearsCase.objects.create(user=user, rule=rule,
                                             status=ArrearsStatus.OPEN)
        self.assertEqual(services.estimate_for_user(user)[0]["case"], case)


class BeatTaskTests(TestCase):
    def test_the_task_returns_a_plain_summary_per_armed_rule(self):
        asset = make_gas_asset(decimals=9)
        make_rule()
        make_rule(metric_code="time.hold", active=False, unit_label="h")
        price_gb_day("0.5")
        user = make_user("alice")
        make_vault_file(user, GB)
        fund_prepaid(user, asset, to_base_units(Decimal("1"), 9))

        result = tasks.run_daily_levy()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["metric_code"], "storage.gb_day")
        self.assertEqual(result[0]["counts"], {services.Outcome.LEVIED: 1})
        self.assertEqual(result[0]["skipped_reason"], "")

    def test_a_second_fire_the_same_day_charges_nobody(self):
        asset = make_gas_asset(decimals=9)
        make_rule()
        price_gb_day("0.5")
        user = make_user("alice")
        make_vault_file(user, GB)
        fund_prepaid(user, asset, to_base_units(Decimal("1"), 9))

        tasks.run_daily_levy()
        after_first = _balance(user, asset)
        again = tasks.run_daily_levy()

        self.assertEqual(again[0]["counts"], {services.Outcome.ALREADY: 1})
        self.assertEqual(_balance(user, asset), after_first)
