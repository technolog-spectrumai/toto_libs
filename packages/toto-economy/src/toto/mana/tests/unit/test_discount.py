"""A member's community discount reaches every mana charge (2026-09-28).

One seam (``toto.tariffs.discounts``, called from ``calculate_tariff_charge``)
feeds the affordability check, the posted charge and the refusal sentence, so
these tests pin all three against the same numbers — plus what may never be
discounted: the subscription's own bill, a price in a currency, a host with no
subscriptions.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.assets.models import Asset
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.mana import services
from toto.mana.tests.fixtures import economy, held, master, seed_prices, spend
from toto.people.models import Person
from toto.quota.charge import InsufficientFunds, charge, check_funds, price_for, refund
from toto.quota.metrics import registry
from toto.socialhub.models import Community
from toto.subscriptions.models import CommunityDiscount
from toto.tariffs import discounts
from toto.tariffs.models import UsageStatus
from toto.tariffs.rate_card import upsert_price
from toto.tariffs.services import ChargeDraft, calculate_tariff_charge

User = get_user_model()


class DiscountArithmeticTests(TestCase):
    def test_rounds_down_to_a_whole_base_unit(self):
        self.assertEqual(discounts.discounted_base_units(3, 50), 1)       # 1.5 → 1
        self.assertEqual(discounts.discounted_base_units(1, 99), 0)
        self.assertEqual(discounts.discounted_base_units(250, 20), 200)

    def test_nothing_off_and_everything_off(self):
        self.assertEqual(discounts.discounted_base_units(7, 0), 7)
        self.assertEqual(discounts.discounted_base_units(7, 100), 0)


@master
class DiscountedChargeTests(TestCase):
    def setUp(self):
        economy()
        seed_prices()
        self.students = Community.objects.create(name="Students")
        self.discount = CommunityDiscount.objects.create(community=self.students, percent=50)
        self.ada = User.objects.create_user("ada", password="pw")
        Person.objects.create(user=self.ada, display_name="ada").communities.add(self.students)
        self.bob = User.objects.create_user("bob", password="pw")   # in no community
        self.tariff = price_for(self.ada, "vault")

    def pay(self, user, code="storage.request", quantity=1):
        return charge(user, self.tariff, code, quantity, unit="request")

    def test_a_member_pays_the_discounted_price(self):
        self.pay(self.ada)
        self.pay(self.bob)
        self.assertEqual(held(self.ada, "storage"), Decimal("99.75"))
        self.assertEqual(held(self.bob, "storage"), Decimal("99.5"))

    def test_the_charge_records_what_was_taken_off(self):
        record, _tx = self.pay(self.ada)
        line = record.charges.get()
        pool = services.pools()["storage"]
        self.assertEqual(line.metadata["discount_percent"], 50)
        self.assertEqual(line.metadata["discount_source"], "Students")
        self.assertEqual(line.metadata["list_amount_base_units"],
                         int(Decimal("0.5") * 10 ** pool.asset.decimals))
        self.assertEqual(line.amount_base_units * 2, line.metadata["list_amount_base_units"])

    def test_the_check_passes_whoever_can_pay_the_discounted_price(self):
        spend(self.ada, "storage", "99.75")                  # 0.25 left: enough at −50 %
        spend(self.bob, "storage", "99.75")                  # the same, at list price
        check_funds(self.ada, self.tariff, "storage.request", 1, "request")
        self.pay(self.ada)
        self.assertEqual(held(self.ada, "storage"), Decimal("0"))
        with self.assertRaises(InsufficientFunds):
            check_funds(self.bob, self.tariff, "storage.request", 1, "request")

    def test_the_refusal_quotes_the_discounted_need(self):
        spend(self.ada, "storage", "99.9")                   # 0.1 left
        with self.assertRaises(InsufficientFunds) as caught:
            check_funds(self.ada, self.tariff, "storage.request", 1, "request")
        self.assertIn("needs 0.25", str(caught.exception))
        self.assertIn("you have 0.1", str(caught.exception))

    def test_a_full_discount_charges_nothing_and_still_records_the_use(self):
        self.discount.percent = 100
        self.discount.save()
        record, tx = self.pay(self.ada)
        self.assertIsNone(tx)
        self.assertEqual(record.status, UsageStatus.POSTED)
        self.assertEqual(record.charges.get().metadata["discount_percent"], 100)
        self.assertEqual(held(self.ada, "storage"), Decimal("100"))

    def test_a_refund_returns_what_was_paid(self):
        record, _tx = self.pay(self.ada)
        self.assertIsNotNone(refund(record))
        self.assertEqual(held(self.ada, "storage"), Decimal("100"))

    def test_the_subscription_bill_is_never_discounted_here(self):
        """settle() has already taken the discount off; a second pass would
        charge a 50 % member a quarter."""
        asr = Asset.objects.get(unit_name="ASR")
        upsert_price(registry.get("subscription.month"), Decimal("5"), asset=asr)
        account, _ = get_or_create_prepaid_account(self.ada)
        tariff = price_for(self.ada, "subscriptions")
        drafts = calculate_tariff_charge(tariff, "subscription.month", Decimal(1), "",
                                         payer_account_id=account.pk)
        self.assertEqual(drafts[0].amount_base_units, 5 * 10 ** asr.decimals)
        # …and by name, even were it ever priced in a pool.
        pool = services.pools()["compute"]
        draft = ChargeDraft(tariff_item=None, quantity=Decimal(1), unit="",
                            amount_base_units=100, payer_account_id=account.pk,
                            receiving_account_id=0, asset_id=pool.asset_id)
        discounts.apply([draft], account.pk, "subscription.month")
        self.assertEqual(draft.amount_base_units, 100)

    def test_a_price_in_a_currency_is_not_discounted(self):
        account, _ = get_or_create_prepaid_account(self.ada)
        asr = Asset.objects.get(unit_name="ASR")
        draft = ChargeDraft(tariff_item=None, quantity=Decimal(1), unit="",
                            amount_base_units=100, payer_account_id=account.pk,
                            receiving_account_id=0, asset_id=asr.pk)
        discounts.apply([draft], account.pk, "storage.request")
        self.assertEqual(draft.amount_base_units, 100)

    def test_a_host_without_subscriptions_charges_the_list_price(self):
        from django.apps import apps

        real = apps.is_installed
        with mock.patch("toto.tariffs.discounts.apps.is_installed",
                        side_effect=lambda name: name != "toto.subscriptions" and real(name)):
            self.pay(self.ada)
        self.assertEqual(held(self.ada, "storage"), Decimal("99.5"))

    def test_the_mana_pages_quote_the_member_price(self):
        self.client.force_login(self.ada)
        page = self.client.get(reverse("mana:about")).content.decode()
        self.assertIn("Your community discount (−50%, Students) is included.", page)
        self.assertIn("−0.25", page)                          # storage.request at −50 %
        self.client.force_login(self.bob)
        page = self.client.get(reverse("mana:about")).content.decode()
        self.assertNotIn("community discount", page)
