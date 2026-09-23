"""A clamped levy charges what the payer holds and never opens a case.

For levies priced in a mana pool: an empty pool refills by itself, so it is a
state, not a debt — and a case would freeze every metered write on the
platform, which an empty security pool must never do.
"""

from decimal import Decimal

from toto.assets.models import AssetHolding, to_base_units
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.testing import LedgerTestCase as TestCase
from toto.tariffs.models import RoundingMode, TariffItem

from .. import arrears, services
from ..models import ArrearsStatus, TaxArrearsCase
from .factories import (GB, fund_prepaid, make_gas_asset, make_rule, make_user,
                        make_vault_file, price_gb_day)


class ClampTests(TestCase):
    def setUp(self):
        self.asset = make_gas_asset(decimals=9)
        self.rule = make_rule()
        self.rule.clamp_to_balance = True
        self.rule.save(update_fields=["clamp_to_balance"])
        price_gb_day("1")                       # 1 a gigabyte-day
        self.user = make_user("alice")
        make_vault_file(self.user, GB)           # so a day costs exactly 1

    def fund(self, amount):
        fund_prepaid(self.user, self.asset, to_base_units(Decimal(amount), 9))

    def balance(self):
        account, _ = get_or_create_prepaid_account(self.user)
        holding = AssetHolding.objects.filter(account=account, asset=self.asset).first()
        return Decimal(holding.balance_base_units if holding else 0) / 10 ** 9

    def open_case(self):
        return TaxArrearsCase.objects.filter(
            user=self.user, status__in=[ArrearsStatus.OPEN, ArrearsStatus.WARNED]
        ).exists()

    def test_a_short_balance_is_charged_exactly_what_it_holds(self):
        self.fund("0.3")
        summary = services.levy_rule(self.rule)
        self.assertEqual(summary.counts, {services.Outcome.CLAMPED: 1})
        self.assertEqual(self.balance(), Decimal("0"))
        self.assertFalse(self.open_case())

    def test_enough_is_charged_in_full(self):
        self.fund("2")
        summary = services.levy_rule(self.rule)
        self.assertEqual(summary.counts, {services.Outcome.LEVIED: 1})
        self.assertEqual(self.balance(), Decimal("1"))

    def test_nothing_held_charges_nothing_and_opens_nothing(self):
        self.fund("0")
        summary = services.levy_rule(self.rule)
        self.assertEqual(summary.counts, {services.Outcome.CLAMPED: 1})
        self.assertFalse(self.open_case())

    def test_a_clamped_day_closes_an_old_case(self):
        arrears.open_or_touch_case(self.user, self.rule, stored_raw=GB)
        self.assertTrue(self.open_case())
        self.fund("0")
        services.levy_rule(self.rule)
        self.assertFalse(self.open_case())

    def test_rounding_to_nearest_never_overdraws(self):
        item = TariffItem.objects.get(metric__code="storage.gb_day")
        item.rounding_mode = RoundingMode.NEAREST
        item.price_per_unit_display = Decimal("0.7")
        item.save()
        self.fund("0.333333333")
        services.levy_rule(self.rule)
        self.assertGreaterEqual(self.balance(), Decimal("0"))
        self.assertFalse(self.open_case())

    def test_an_unclamped_rule_still_opens_a_case(self):
        """The regression guard: the clamp is opt-in, per rule."""
        self.rule.clamp_to_balance = False
        self.rule.save(update_fields=["clamp_to_balance"])
        self.fund("0.3")
        summary = services.levy_rule(self.rule)
        self.assertEqual(summary.counts, {services.Outcome.FAILED: 1})
        self.assertTrue(self.open_case())
