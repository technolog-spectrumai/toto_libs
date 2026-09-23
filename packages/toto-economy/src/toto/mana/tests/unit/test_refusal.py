"""An empty pool refuses in mana words, with when it will be enough again."""

from decimal import Decimal
from itertools import count

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.assets.models import Asset
from toto.assets.prepaid import get_or_create_prepaid_account
from toto.assets.services.assets import transfer_asset
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform
from toto.mana import services
from toto.mana.tests.fixtures import seed_prices
from toto.quota.charge import InsufficientFunds, check_funds, price_for
from toto.tariffs.models import TariffItem

MASTER = dict(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
_ref = count()


@override_settings(**MASTER)
class RefusalTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        seed_prices()
        self.pools = services.pools()
        self.ada = get_user_model().objects.create_user("ada", password="pw")

    def drain(self, role, amount):
        pool = self.pools[role]
        account, _ = get_or_create_prepaid_account(self.ada)
        transfer_asset(asset=pool.asset, sender_account=account,
                       receiver_account=pool.asset.reserve_account,
                       amount=Decimal(amount), reference=f"test-drain-{next(_ref)}")

    def refusal(self, app_label, code, quantity=1):
        tariff = price_for(self.ada, app_label)
        with self.assertRaises(InsufficientFunds) as caught:
            check_funds(self.ada, tariff, code, quantity)
        return caught.exception

    def test_an_empty_pool_names_itself_and_when_it_will_be_enough(self):
        self.drain("storage", "99.8")                    # 0.2 left; a request is 0.5
        exc = self.refusal("vault", "storage.request")
        text = str(exc)
        self.assertIn("storage mana", text)
        self.assertIn("needs 0.5", text)
        self.assertIn("you have 0.2", text)
        self.assertIn("about 1 h", text)
        self.assertEqual(exc.status_code, 402)
        self.assertEqual(exc.asset_name, "GREEN")       # structured data kept

    def test_a_cost_above_a_full_pool_says_waiting_will_not_help(self):
        item = TariffItem.objects.get(metric__code="storage.request")
        item.price_per_unit_display = Decimal("150")
        item.save()
        text = str(self.refusal("vault", "storage.request"))
        self.assertIn("more than a full pool holds", text)

    def test_a_non_mana_price_keeps_the_old_wording(self):
        from toto.quota.metrics import registry
        from toto.tariffs.rate_card import upsert_price

        asr = Asset.objects.get(unit_name="ASR")
        upsert_price(registry.get("subscription.month"), Decimal("5"), asset=asr)
        text = str(self.refusal("subscriptions", "subscription.month"))
        self.assertTrue(text.startswith("Insufficient ASR"), text)

    def test_an_empty_security_pool_does_not_block_compute(self):
        self.drain("security", "100")
        tariff = price_for(self.ada, "workflows")
        check_funds(self.ada, tariff, "workflows.run", 1)   # must not raise
