"""The billing gateway's less-travelled doors: check-and-charge in one step,
the stipend (``credit``), and ``refund_for`` — the refund a worker holding
only a domain pk can ask for. Each must be a no-op where nothing bills and
exact where something does: the newest live charge, the right metric, once.
"""

import unittest
from decimal import Decimal
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.quota import charge as gateway

User = get_user_model()


class UnbilledHostTests(TestCase):
    """Where the registry says tariffs is absent, every door is shut quietly."""

    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.unbilled = mock.patch.object(gateway, "billing_enabled", return_value=False)

    def test_there_is_no_price(self):
        with self.unbilled:
            self.assertIsNone(gateway.price_for(self.ada, "vault"))

    def test_there_is_nothing_to_refund(self):
        with self.unbilled:
            self.assertIsNone(gateway.refund(object()))
            self.assertIsNone(gateway.refund_for("vault.VaultFile", 1))

    def test_no_tariff_means_no_credit_and_no_charge(self):
        self.assertIsNone(gateway.credit(self.ada, None, "x.y", 1))
        self.assertIsNone(gateway.check_and_charge(self.ada, None, "x.y", 1))


class GatewayTestCase(TestCase):
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
        self.tariff = gateway.price_for(self.ada, "vault")

    def held(self, role="storage"):
        from toto.mana.tests.fixtures import held

        return held(self.ada, role)

    def pay(self, code="storage.request", quantity=1, source_id="1"):
        return gateway.charge(self.ada, self.tariff, code, quantity, unit="request",
                              source_type="vault.VaultFile", source_id=source_id)


class CheckAndChargeTests(GatewayTestCase):
    def test_it_charges_when_the_pool_can_pay(self):
        record, tx = gateway.check_and_charge(self.ada, self.tariff, "storage.request", 2,
                                              unit="request")
        self.assertIsNotNone(tx)
        self.assertEqual(self.held(), Decimal("99"))

    def test_it_refuses_before_writing_anything(self):
        from toto.mana.tests.fixtures import spend
        from toto.tariffs.models import UsageRecord

        spend(self.ada, "storage", "99.8")
        with self.assertRaises(gateway.InsufficientFunds):
            gateway.check_and_charge(self.ada, self.tariff, "storage.request", 1, unit="request")
        self.assertFalse(UsageRecord.objects.exists())
        self.assertEqual(self.held(), Decimal("0.2"))


class RefundForTests(GatewayTestCase):
    def test_the_live_charge_for_the_row_is_reversed(self):
        self.pay(source_id="41")
        self.assertIsNotNone(gateway.refund_for("vault.VaultFile", 41, reason="upload failed"))
        self.assertEqual(self.held(), Decimal("100"))

    def test_a_row_with_no_charge_has_nothing_to_refund(self):
        self.assertIsNone(gateway.refund_for("vault.VaultFile", 999))

    def test_asking_twice_refunds_once(self):
        self.pay(source_id="7")
        gateway.refund_for("vault.VaultFile", 7)
        self.assertIsNone(gateway.refund_for("vault.VaultFile", 7))
        self.assertEqual(self.held(), Decimal("100"))

    def test_the_newest_charge_is_the_one_reversed(self):
        from toto.tariffs.models import UsageStatus

        older, _ = self.pay(source_id="5")
        newer, _ = self.pay(quantity=3, source_id="5")
        gateway.refund_for("vault.VaultFile", 5)
        older.refresh_from_db()
        newer.refresh_from_db()
        self.assertEqual((older.status, newer.status), (UsageStatus.POSTED, UsageStatus.REVERSED))
        self.assertEqual(self.held(), Decimal("99.5"))

    def test_the_metric_picks_between_charges_on_one_row(self):
        from toto.tariffs.models import UsageStatus

        request, _ = self.pay(source_id="9")
        transfer, _ = gateway.charge(self.ada, self.tariff, "storage.transfer_mb", 10, unit="mb",
                                     source_type="vault.VaultFile", source_id="9")
        gateway.refund_for("vault.VaultFile", 9, "storage.request")
        request.refresh_from_db()
        transfer.refresh_from_db()
        self.assertEqual(request.status, UsageStatus.REVERSED)
        self.assertEqual(transfer.status, UsageStatus.POSTED)

    def test_another_rows_charge_is_never_touched(self):
        self.pay(source_id="1")
        self.assertIsNone(gateway.refund_for("vault.VaultFile", 2))
        self.assertIsNone(gateway.refund_for("forum.Message", 1))
        self.assertEqual(self.held(), Decimal("99.5"))

    def test_the_reason_is_the_refund_description(self):
        self.pay(source_id="3")
        reversal = gateway.refund_for("vault.VaultFile", 3, reason="antivirus rejected it")
        self.assertEqual(reversal.description, "antivirus rejected it")


class CreditTests(GatewayTestCase):
    def test_a_stipend_is_paid_through_the_gateway(self):
        from toto.assets.models import AssetHolding
        from toto.mana import services
        from toto.mana.tests.fixtures import spend
        from toto.tariffs.rate_card import revenue_account

        pool = services.pools()["storage"]
        treasury = revenue_account()
        holding, _ = AssetHolding.objects.get_or_create(account=treasury, asset=pool.asset)
        holding.balance_base_units = 10 * 10 ** pool.asset.decimals
        holding.save()
        spend(self.ada, "storage", "5")
        tx = gateway.credit(self.ada, self.tariff, "storage.request", 4, unit="request",
                            reference="stipend-ada-1")
        self.assertIsNotNone(tx)
        self.assertEqual(self.held(), Decimal("97"))

    def test_a_stipend_is_paid_at_the_list_price_whatever_the_discount(self):
        """The discount lowers what a member PAYS; a credit has no payer."""
        from toto.assets.models import AssetHolding
        from toto.mana import services
        from toto.mana.tests.fixtures import spend
        from toto.people.models import Person
        from toto.socialhub.models import Community
        from toto.subscriptions.models import CommunityDiscount
        from toto.tariffs.rate_card import revenue_account

        students = Community.objects.create(name="Students")
        CommunityDiscount.objects.create(community=students, percent=50)
        Person.objects.create(user=self.ada, display_name="ada").communities.add(students)
        pool = services.pools()["storage"]
        holding, _ = AssetHolding.objects.get_or_create(account=revenue_account(), asset=pool.asset)
        holding.balance_base_units = 10 * 10 ** pool.asset.decimals
        holding.save()
        spend(self.ada, "storage", "5")
        gateway.credit(self.ada, self.tariff, "storage.request", 2, unit="request",
                       reference="stipend-ada-2")
        self.assertEqual(self.held(), Decimal("96"))
