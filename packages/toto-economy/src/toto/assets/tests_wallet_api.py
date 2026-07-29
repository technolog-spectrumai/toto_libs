from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.assets.models import Asset, LedgerAccount, AssetHolding, AccountType

User = get_user_model()


def _make_asset(unit_name="TST"):
    return Asset.objects.create(
        name=f"Test Asset {unit_name}",
        unit_name=unit_name,
        decimals=2,
        total_supply_base_units=1_000_000,
    )


class WalletSummaryApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="walletuser", password="pass")
        self.other = User.objects.create_user(username="walletother", password="pass")

    def test_unauthenticated(self):
        res = self.client.get("/assets/api/wallet/summary/")
        self.assertEqual(res.status_code, 401)

    def test_empty_wallet(self):
        self.client.force_login(self.user)
        res = self.client.get("/assets/api/wallet/summary/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("accounts", data)
        self.assertIn("pending_charges", data)
        self.assertIn("open_invoices", data)
        self.assertIn("totals", data)
        # A prepaid account is auto-created per user via signal — accounts may not be empty
        self.assertIsInstance(data["accounts"], list)
        self.assertEqual(data["pending_charges"], [])
        self.assertEqual(data["open_invoices"], [])

    def test_own_accounts_only(self):
        asset = _make_asset()
        own_acct = LedgerAccount.objects.create(
            code="OWN001", name="My Account", account_type=AccountType.USER, user=self.user
        )
        other_acct = LedgerAccount.objects.create(
            code="OTH001", name="Other Account", account_type=AccountType.USER, user=self.other
        )
        AssetHolding.objects.create(asset=asset, account=own_acct, balance_base_units=500)
        AssetHolding.objects.create(asset=asset, account=other_acct, balance_base_units=200)

        self.client.force_login(self.user)
        res = self.client.get("/assets/api/wallet/summary/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        codes = [a["code"] for a in data["accounts"]]
        # Own account must be included; other user's account must NOT be included
        self.assertIn("OWN001", codes)
        self.assertNotIn("OTH001", codes)

    def test_holdings_balance_display(self):
        asset = _make_asset("TOK")
        acct = LedgerAccount.objects.create(
            code="BAL001", name="Balance Test", account_type=AccountType.USER, user=self.user
        )
        AssetHolding.objects.create(asset=asset, account=acct, balance_base_units=1050)

        self.client.force_login(self.user)
        res = self.client.get("/assets/api/wallet/summary/")
        holding = res.json()["accounts"][0]["holdings"][0]
        self.assertEqual(holding["asset_unit"], "TOK")
        self.assertEqual(holding["balance_base_units"], 1050)
        self.assertEqual(Decimal(holding["balance_display"]), Decimal("10.50"))

    def test_totals_structure(self):
        self.client.force_login(self.user)
        res = self.client.get("/assets/api/wallet/summary/")
        totals = res.json()["totals"]
        self.assertIn("total_pending_by_asset", totals)
        self.assertIn("total_open_invoice_amount", totals)
