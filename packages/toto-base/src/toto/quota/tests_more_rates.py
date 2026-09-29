"""``toto.quota.rates`` — the quoting side of metering — at its edges.

The display rules (three significant digits; a member's discount on a mana
price), who counts as an economy operator and who is shown mana instead, and
the rate-desk writes: a price edit that must stay in its mana pool, a
currency switch that must leave the pools alone, and the refusals a raw form
value can meet. What a member actually paid is read from the ledger.

toto-base ships where toto-economy does not, so every class that needs the
ledger skips there and imports it lazily.
"""

import unittest
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TestCase, override_settings

from toto.quota import rates

User = get_user_model()


def without(app_label):
    real = apps.is_installed
    return mock.patch.object(apps, "is_installed",
                             side_effect=lambda name: name != app_label and real(name))


class SignificantEdgeTests(SimpleTestCase):
    def test_the_digit_count_is_a_parameter(self):
        self.assertEqual(rates.significant("0.123456", 2), "0.12")
        self.assertEqual(rates.significant("0.123456", 5), "0.12346")

    def test_rounding_can_carry_into_a_new_digit(self):
        self.assertEqual(rates.significant("0.0009995"), "0.001")
        self.assertEqual(rates.significant("9.995"), "10")

    def test_a_negative_amount_rounds_half_away_from_zero(self):
        self.assertEqual(rates.significant("-1234.5"), "-1235")
        self.assertEqual(rates.significant("-0.0001235"), "-0.000124")

    def test_a_negative_zero_reads_as_zero(self):
        self.assertEqual(rates.significant("-0.000"), "0")

    def test_numbers_of_every_python_type(self):
        self.assertEqual(rates.significant(0.1), "0.1")
        self.assertEqual(rates.significant(1500), "1500")
        self.assertEqual(rates.significant(Decimal("1E+3")), "1000")

    def test_not_a_finite_number_passes_through(self):
        self.assertEqual(rates.significant(Decimal("NaN")), "NaN")
        self.assertEqual(rates.significant("Infinity"), "Infinity")

    def test_the_sig3_filter_is_empty_for_nothing(self):
        from django.template import Context, Template

        out = Template("{% load quota_tags %}[{{ v|sig3 }}]").render(Context({"v": None}))
        self.assertEqual(out, "[]")


class DiscountedDisplayTests(SimpleTestCase):
    def test_no_percent_is_the_amount_itself_as_a_decimal(self):
        for percent in (None, 0, -10):
            with self.subTest(percent=percent):
                self.assertEqual(rates.discounted("0.5", percent), Decimal("0.5"))
        self.assertIsInstance(rates.discounted(2, 0), Decimal)

    def test_a_full_or_larger_discount_is_free(self):
        self.assertEqual(rates.discounted("0.5", 100), Decimal(0))
        self.assertEqual(rates.discounted("0.5", 150), Decimal(0))

    def test_an_odd_percent_is_exact_for_display(self):
        self.assertEqual(rates.discounted("0.1", 33), Decimal("0.067"))
        self.assertEqual(rates.discounted(0.2, 50), Decimal("0.1"))


class MemberDiscountTests(TestCase):
    @classmethod
    def setUpClass(cls):
        if not apps.is_installed("toto.subscriptions"):
            raise unittest.SkipTest("no subscriptions on this host")
        super().setUpClass()

    def setUp(self):
        from toto.people.models import Person
        from toto.socialhub.models import Community
        from toto.subscriptions.models import CommunityDiscount

        self.students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=self.students, percent=30)
        self.ada = User.objects.create_user("ada", password="pw")
        Person.objects.create(user=self.ada, display_name="ada").communities.add(self.students)

    def test_a_member_is_quoted_their_best_community(self):
        self.assertEqual(rates.member_discount(self.ada), (30, "Students"))

    def test_nobody_signed_in_has_no_discount(self):
        self.assertEqual(rates.member_discount(AnonymousUser()), (0, ""))
        self.assertEqual(rates.member_discount(None), (0, ""))

    def test_a_host_without_subscriptions_quotes_list_prices(self):
        with without("toto.subscriptions"):
            self.assertEqual(rates.member_discount(self.ada), (0, ""))

    def test_a_person_outside_every_community_has_none(self):
        bob = User.objects.create_user("bob", password="pw")
        self.assertEqual(rates.member_discount(bob), (0, ""))


class EconomyVisibilityTests(TestCase):
    """Who is shown mana instead of the economy (2026-09-28)."""

    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.stan = User.objects.create_user("stan", password="pw", is_staff=True)
        self.root = User.objects.create_superuser("root", password="pw")

    def test_nobody_signed_in_is_no_operator(self):
        self.assertFalse(rates.economy_operator(AnonymousUser()))
        self.assertFalse(rates.economy_operator(None))

    def test_staff_are_members_here(self):
        self.assertFalse(rates.economy_operator(self.stan))

    def test_a_superuser_is_an_operator_where_no_plan_is_sold(self):
        with without("toto.subscriptions"):
            self.assertTrue(rates.economy_operator(self.root))

    def test_where_a_plan_is_sold_the_operator_is_what_the_plan_says(self):
        if not apps.is_installed("toto.subscriptions"):
            self.skipTest("no subscriptions on this host")
        with mock.patch("toto.subscriptions.models.superuser_plan_active",
                        return_value=False) as asked:
            self.assertFalse(rates.economy_operator(self.root))
        asked.assert_called_once_with(self.root)

    def test_a_host_without_mana_hides_the_economy_from_nobody(self):
        with without("toto.mana"):
            self.assertFalse(rates.economy_hidden_from(self.ada))

    @override_settings(ECONOMY_STAFF_ONLY=False)
    def test_the_host_switch_opens_the_economy_to_everybody(self):
        if not apps.is_installed("toto.mana"):
            self.skipTest("no mana on this host")
        self.assertFalse(rates.economy_hidden_from(self.ada))

    @override_settings(ECONOMY_STAFF_ONLY=True)
    def test_on_a_mana_host_a_member_and_staff_see_mana(self):
        if not apps.is_installed("toto.mana"):
            self.skipTest("no mana on this host")
        self.assertTrue(rates.economy_hidden_from(self.ada))
        self.assertTrue(rates.economy_hidden_from(self.stan))
        with mock.patch.object(rates, "economy_operator", return_value=True):
            self.assertFalse(rates.economy_hidden_from(self.root))


class EconomyTestCase(TestCase):
    """A master host with the three pools and the seeded mana prices."""

    @classmethod
    def setUpClass(cls):
        if not apps.is_installed("toto.tariffs") or not apps.is_installed("toto.mana"):
            raise unittest.SkipTest("no economy on this host")
        from toto.assets.testing import TEST_ISSUER_KEY

        master = override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY,
                                   ASSETS_MONETARY_MASTER=True)
        master.enable()
        cls.addClassCleanup(master.disable)
        super().setUpClass()

    def setUp(self):
        from toto.mana.tests.fixtures import economy, seed_prices

        economy()
        seed_prices()
        self.ada = User.objects.create_user("ada", password="pw")

    def pool_asset(self, role):
        from toto.mana import services

        return services.pools()[role].asset


class RateDeskWriteTests(EconomyTestCase):
    def item(self, code):
        from toto.tariffs.models import TariffItem
        from toto.tariffs.rate_card import DEFAULT_TARIFF_CODE

        return TariffItem.objects.get(tariff__code=DEFAULT_TARIFF_CODE, metric__code=code)

    def test_a_mana_price_edited_on_the_desk_stays_in_its_pool(self):
        self.assertTrue(rates.set_price("storage.request", "0.7"))
        item = self.item("storage.request")
        self.assertEqual(item.charged_asset, self.pool_asset("storage"))
        self.assertEqual(item.price_per_unit_display, Decimal("0.7"))

    def test_a_blank_price_makes_the_metric_free_by_removing_it(self):
        self.assertTrue(rates.set_price("storage.request", "  "))
        self.assertIsNone(rates.price_of("storage.request"))
        self.assertFalse(rates.clear_price("storage.request"))       # nothing left to remove

    def test_a_malformed_or_negative_price_is_the_callers_mistake(self):
        for raw in ("abc", "-1"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                rates.set_price("storage.request", raw)
        self.assertEqual(self.item("storage.request").price_per_unit_display, Decimal("0.5"))

    def test_an_unregistered_code_writes_nothing(self):
        self.assertFalse(rates.set_price("no.such.metric", "1"))

    def test_a_stale_currency_choice_falls_back_rather_than_failing(self):
        self.assertTrue(rates.set_price("storage.request", "0.6", asset_id=10 ** 9))
        self.assertEqual(self.item("storage.request").charged_asset, self.pool_asset("storage"))

    def test_an_inactive_currency_choice_is_not_used(self):
        from toto.assets.models import Asset

        asr = Asset.objects.get(unit_name="ASR")
        Asset.objects.filter(pk=asr.pk).update(active=False)
        self.assertIsNone(rates._asset_by_id(asr.pk))
        self.assertIsNone(rates._asset_by_id(None))

    def test_a_host_with_no_billing_asset_prices_nothing(self):
        from toto.tariffs.rate_card import NoGasAsset

        with mock.patch("toto.tariffs.rate_card.upsert_price", side_effect=NoGasAsset("none")):
            self.assertFalse(rates.set_price("storage.request", "1"))

    def test_the_currency_switch_leaves_every_mana_price_in_its_pool(self):
        from toto.assets.models import Asset
        from toto.quota.metrics import registry
        from toto.tariffs.rate_card import upsert_price

        asr = Asset.objects.get(unit_name="ASR")
        upsert_price(registry.get("subscription.month"), Decimal("5"), asset=asr)
        other = Asset.objects.filter(active=True).exclude(pk=asr.pk).exclude(
            pk__in=[self.pool_asset(r).pk for r in ("security", "compute", "storage")]).first()
        if other is None:
            self.skipTest("only the settlement asset is active here")
        self.assertEqual(rates.set_charging_currency(other.pk), other.unit_name)
        self.assertEqual(self.item("storage.request").charged_asset, self.pool_asset("storage"))
        self.assertEqual(self.item("workflows.run").charged_asset, self.pool_asset("compute"))
        self.assertEqual(self.item("subscription.month").charged_asset, other)

    def test_an_inactive_currency_cannot_be_chosen(self):
        from toto.assets.models import Asset

        asr = Asset.objects.get(unit_name="ASR")
        Asset.objects.filter(pk=asr.pk).update(active=False)
        with self.assertRaises(ValueError):
            rates.set_charging_currency(asr.pk)

    def test_the_billing_assets_are_the_active_ones_by_ticker(self):
        from toto.assets.models import Asset

        listed = rates.billing_assets()
        symbols = [a["symbol"] for a in listed]
        self.assertEqual(symbols, sorted(symbols))
        self.assertEqual(set(symbols), set(Asset.objects.filter(active=True)
                                           .values_list("unit_name", flat=True)))
        self.assertTrue(all(set(a) == {"id", "symbol", "name"} for a in listed))

    def test_the_advanced_link_exists_only_for_a_priced_metric(self):
        self.assertTrue(rates.advanced_url("storage.request"))
        self.assertEqual(rates.advanced_url("no.such.metric"), "")

    def test_the_wallet_link_reverses(self):
        from django.urls import reverse

        self.assertEqual(rates.wallet_url(), reverse("assets:wallet"))


class WhatWasPaidTests(EconomyTestCase):
    def pay(self, user, code="storage.request", quantity=1):
        from toto.quota.charge import charge, price_for

        return charge(user, price_for(user, "vault"), code, quantity, unit="request")

    def test_the_spend_is_summed_from_the_ledger_per_metric(self):
        self.pay(self.ada)
        self.pay(self.ada, quantity=2)
        spent = rates.spend_by_metric(self.ada)
        self.assertEqual(spent["storage.request"]["amount"], Decimal("1.5"))
        self.assertEqual(spent["storage.request"]["asset"], self.pool_asset("storage").unit_name)

    def test_a_discounted_member_is_shown_what_they_paid_not_the_list(self):
        from toto.people.models import Person
        from toto.socialhub.models import Community
        from toto.subscriptions.models import CommunityDiscount

        students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=students, percent=50)
        Person.objects.create(user=self.ada, display_name="ada").communities.add(students)
        self.pay(self.ada, quantity=2)
        self.assertEqual(rates.spend_by_metric(self.ada)["storage.request"]["amount"],
                         Decimal("0.5"))

    def test_another_members_spend_is_not_theirs(self):
        bob = User.objects.create_user("bob", password="pw")
        self.pay(bob)
        self.assertEqual(rates.spend_by_metric(self.ada), {})

    def test_since_excludes_what_came_before(self):
        from datetime import timedelta

        from django.utils import timezone

        self.pay(self.ada)
        self.assertEqual(rates.spend_by_metric(self.ada, since=timezone.now() + timedelta(minutes=1)),
                         {})

    def test_nobody_signed_in_has_spent_nothing(self):
        self.assertEqual(rates.spend_by_metric(AnonymousUser()), {})
        self.assertIsNone(rates.balance_of(None))

    def test_the_gas_balance_is_read_from_the_billing_account(self):
        balance = rates.balance_of(self.ada)
        from toto.tariffs.rate_card import gas_asset

        self.assertEqual(balance["asset"], gas_asset().unit_name)
        self.assertIsInstance(balance["amount"], Decimal)

    def test_no_gas_asset_is_no_balance(self):
        with mock.patch("toto.tariffs.rate_card.gas_asset", return_value=None):
            self.assertIsNone(rates.balance_of(self.ada))

    def test_the_quote_and_the_charge_agree_to_the_digits_shown(self):
        """Display rounds, the charge floors to a base unit; a member reads the
        same number from both."""
        from toto.tariffs.discounts import discounted_base_units

        asset = self.pool_asset("storage")
        scale = 10 ** asset.decimals
        for price, percent in (("0.5", 33), ("0.2", 7), ("20", 45)):
            with self.subTest(price=price, percent=percent):
                charged = Decimal(discounted_base_units(int(Decimal(price) * scale), percent)) / scale
                self.assertEqual(rates.significant(rates.discounted(price, percent)),
                                 rates.significant(charged))
