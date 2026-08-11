"""Trading is central to the master. A branch is refused, and told where to go.

The exchange services ship in the wheel to every host that installs the ledger,
so "the branch has no bourse UI" is not protection: a shell or a management
command reaches them just as well.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError

from toto.assets.models import CurrencyIssuer
from toto.assets.services.assets import (get_exchange_rate,
                                         quote_currency_payment,
                                         quote_exchange)
from toto.assets.testing import LedgerTestCase as TestCase, make_asset


class MasterMayTradeTests(TestCase):
    def setUp(self):
        self.one = make_asset(unit_name="AAA")
        self.two = make_asset(unit_name="BBB")

    def test_the_same_asset_is_a_rate_of_one(self):
        _, rate, commission, source = get_exchange_rate(self.one, self.one)
        self.assertEqual(rate, Decimal("1"))
        self.assertEqual(source, "same_asset")

    def test_a_cross_pair_still_refuses_and_names_the_bourse(self):
        # The no-FX rule, unchanged: negotiated exchange happens at the desk,
        # never as an implicit rate inside a billing path.
        with self.assertRaises(ValidationError) as caught:
            get_exchange_rate(self.one, self.two)
        self.assertIn("bourse", "; ".join(caught.exception.messages))


class BranchMayNotTradeTests(TestCase):
    def setUp(self):
        self.one = make_asset(unit_name="AAA")
        self.two = make_asset(unit_name="BBB")
        # Become a branch: the authority row survives, the private half does
        # not — exactly what a host that pins its master looks like.
        CurrencyIssuer.objects.filter(is_self=True).update(
            private_key_encrypted=None)

    def _assert_refused(self, call):
        with self.assertRaises(ValidationError) as caught:
            call()
        message = "; ".join(caught.exception.messages)
        self.assertIn("master platform", message)
        return message

    def test_get_exchange_rate_is_refused(self):
        self._assert_refused(lambda: get_exchange_rate(self.one, self.two))

    def test_even_the_same_asset_pair_is_refused(self):
        # The guard runs BEFORE the same-asset shortcut: a branch has no
        # business in the exchange path at all, not even for a trivial answer.
        self._assert_refused(lambda: get_exchange_rate(self.one, self.one))

    def test_quote_exchange_is_refused(self):
        self._assert_refused(
            lambda: quote_exchange(from_asset=self.one, to_asset=self.two,
                                   amount=Decimal("1")))

    def test_quote_currency_payment_is_refused(self):
        self._assert_refused(
            lambda: quote_currency_payment(currency_code="AAA",
                                           payment_asset=self.two,
                                           amount=Decimal("1")))

    def test_the_refusal_says_where_trading_happens(self):
        message = self._assert_refused(
            lambda: get_exchange_rate(self.one, self.two))
        self.assertIn("bourse proposal on the master", message)
