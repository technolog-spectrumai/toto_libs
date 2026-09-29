"""The wallet PIN: what unlocks a member's wallet for five minutes.

The PIN itself (stored sealed, checked, replaced), the session mark and the
bearer token a desktop client gets (both expire, both belong to one person),
and the doors that take a PIN — the portal's form and JSON check, and the
wallet API's. Plus the two wallet JSON reads the same client polls: its
movements and its pending charges.
"""

import json
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from toto.assets import wallet_pin
from toto.assets.models import AccountType, AssetHolding, LedgerAccount, LedgerEntry, WalletPin
from toto.assets.testing import LedgerTestCase as TestCase
from toto.assets.testing import make_asset

User = get_user_model()


class PinTokenTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.addCleanup(wallet_pin._PIN_TOKENS.clear)

    def test_a_token_opens_the_wallet_of_the_person_it_was_issued_to(self):
        token = wallet_pin.issue_pin_token(self.ada)
        self.assertTrue(wallet_pin.verify_pin_token(token, self.ada))

    def test_a_token_is_useless_to_anybody_else(self):
        token = wallet_pin.issue_pin_token(self.ada)
        self.assertFalse(wallet_pin.verify_pin_token(token, self.bob))

    def test_an_unknown_token_opens_nothing(self):
        self.assertFalse(wallet_pin.verify_pin_token("made-up", self.ada))

    def test_a_token_expires_after_five_minutes(self):
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=1_000_000.0):
            token = wallet_pin.issue_pin_token(self.ada)
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=1_000_000.0 + 299):
            self.assertTrue(wallet_pin.verify_pin_token(token, self.ada))
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=1_000_000.0 + 301):
            self.assertFalse(wallet_pin.verify_pin_token(token, self.ada))

    def test_issuing_a_token_sweeps_out_the_expired_ones(self):
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=1_000_000.0):
            stale = wallet_pin.issue_pin_token(self.ada)
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=1_000_000.0 + 1000):
            fresh = wallet_pin.issue_pin_token(self.bob)
        self.assertNotIn(stale, wallet_pin._PIN_TOKENS)
        self.assertIn(fresh, wallet_pin._PIN_TOKENS)

    def test_every_token_is_different(self):
        self.assertNotEqual(wallet_pin.issue_pin_token(self.ada), wallet_pin.issue_pin_token(self.ada))


class PinSessionTests(TestCase):
    def test_a_verified_session_lasts_five_minutes(self):
        session = {}
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=5000.0):
            wallet_pin.mark_session_verified(session)
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=5000.0 + 299):
            self.assertTrue(wallet_pin.session_is_verified(session))
        with mock.patch("toto.assets.wallet_pin.time.time", return_value=5000.0 + 300):
            self.assertFalse(wallet_pin.session_is_verified(session))

    def test_an_unmarked_session_is_not_verified(self):
        self.assertFalse(wallet_pin.session_is_verified({}))

    def test_clearing_ends_it(self):
        session = {}
        wallet_pin.mark_session_verified(session)
        wallet_pin.clear_session(session)
        self.assertFalse(wallet_pin.session_is_verified(session))
        wallet_pin.clear_session(session)                  # twice is harmless


class PinStorageTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")

    def test_the_right_pin_passes_and_a_wrong_one_does_not(self):
        wallet_pin.set_wallet_pin(self.ada, "4821")
        self.assertTrue(wallet_pin.has_wallet_pin(self.ada))
        self.assertTrue(wallet_pin.check_wallet_pin(self.ada, "4821"))
        self.assertFalse(wallet_pin.check_wallet_pin(self.ada, "4822"))
        self.assertFalse(wallet_pin.check_wallet_pin(self.ada, ""))

    def test_a_member_with_no_pin_is_not_asked_for_one(self):
        self.assertFalse(wallet_pin.has_wallet_pin(self.ada))
        self.assertTrue(wallet_pin.check_wallet_pin(self.ada, "anything"))

    def test_a_new_pin_replaces_the_old_one(self):
        wallet_pin.set_wallet_pin(self.ada, "1111")
        wallet_pin.set_wallet_pin(self.ada, "2222")
        self.assertEqual(WalletPin.objects.filter(user=self.ada).count(), 1)
        self.assertFalse(wallet_pin.check_wallet_pin(self.ada, "1111"))
        self.assertTrue(wallet_pin.check_wallet_pin(self.ada, "2222"))

    def test_the_pin_is_not_stored_in_the_clear(self):
        wallet_pin.set_wallet_pin(self.ada, "73519")
        secret = WalletPin.objects.get(user=self.ada).secret
        stored = " ".join(str(getattr(secret, f.attname)) for f in secret._meta.concrete_fields)
        self.assertNotIn("73519", stored)

    def test_one_members_pin_does_not_open_another_wallet(self):
        bob = User.objects.create_user("bob", password="pw")
        wallet_pin.set_wallet_pin(self.ada, "1111")
        wallet_pin.set_wallet_pin(bob, "2222")
        self.assertFalse(wallet_pin.check_wallet_pin(bob, "1111"))

    def test_a_pin_sealed_under_another_secret_fails_closed(self):
        wallet_pin.set_wallet_pin(self.ada, "1111")
        with override_settings(WALLET_VAULT_SECRET="rotated"), \
                self.assertLogs("toto.assets.wallet_pin", level="ERROR"):
            self.assertFalse(wallet_pin.check_wallet_pin(self.ada, "1111"))


class PinPortalDoorTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.get_or_create(
            site_name="Test", defaults={"author": "t", "publication_year": 2026, "active": True})
        self.ada = User.objects.create_user("ada", password="pw")
        self.client.force_login(self.ada)
        self.set_url = reverse("assets:wallet_pin_set")
        self.verify_url = reverse("assets:wallet_pin_verify")

    def post_pin(self, pin, confirm=None):
        return self.client.post(self.set_url, {"pin": pin, "pin_confirm": pin if confirm is None else confirm})

    def test_an_empty_short_or_mismatched_pin_is_refused(self):
        for pin, confirm in (("", ""), ("123", "123"), ("12345", "12346")):
            with self.subTest(pin=pin, confirm=confirm):
                response = self.post_pin(pin, confirm)
                self.assertEqual(response.status_code, 200)
                self.assertFalse(response.context["has_pin"])
        self.assertFalse(wallet_pin.has_wallet_pin(self.ada))

    def test_a_good_pin_is_saved_and_the_form_redirects(self):
        response = self.post_pin("  4821  ")
        self.assertRedirects(response, self.set_url, fetch_redirect_response=False)
        self.assertTrue(wallet_pin.check_wallet_pin(self.ada, "4821"))

    def test_a_failing_save_is_reported_not_raised(self):
        with mock.patch("toto.assets.wallet_pin.set_wallet_pin", side_effect=RuntimeError("kdf")):
            response = self.post_pin("4821")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Could not save PIN")

    def test_the_json_check_takes_only_a_post(self):
        self.assertEqual(self.client.get(self.verify_url).status_code, 405)

    def test_the_json_check_refuses_garbage_and_an_empty_pin(self):
        bad = self.client.post(self.verify_url, "not json", content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        empty = self.client.post(self.verify_url, json.dumps({"pin": ""}),
                                 content_type="application/json")
        self.assertEqual(empty.json(), {"ok": False, "error": "PIN is required."})

    def test_the_right_pin_marks_the_session_and_a_wrong_one_does_not(self):
        wallet_pin.set_wallet_pin(self.ada, "4821")
        wrong = self.client.post(self.verify_url, json.dumps({"pin": "0000"}),
                                 content_type="application/json")
        self.assertEqual(wrong.json()["ok"], False)
        self.assertFalse(wallet_pin.session_is_verified(self.client.session))
        right = self.client.post(self.verify_url, json.dumps({"pin": "4821"}),
                                 content_type="application/json")
        self.assertEqual(right.json(), {"ok": True})
        self.assertTrue(wallet_pin.session_is_verified(self.client.session))

    def test_anonymous_cannot_set_a_pin(self):
        self.client.logout()
        response = self.client.post(self.set_url, {"pin": "4821", "pin_confirm": "4821"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(WalletPin.objects.exists())


class PinApiTests(TestCase):
    URL = "/assets/api/wallet/pin/verify/"

    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.addCleanup(wallet_pin._PIN_TOKENS.clear)

    def post(self, body):
        return self.client.post(self.URL, body if isinstance(body, str) else json.dumps(body),
                                content_type="application/json")

    def test_anonymous_is_401(self):
        self.assertEqual(self.post({"pin": "1"}).status_code, 401)

    def test_garbage_is_400_and_an_empty_pin_is_refused(self):
        self.client.force_login(self.ada)
        self.assertEqual(self.post("{").status_code, 400)
        self.assertEqual(self.post({}).json()["error"], "PIN is required.")

    def test_no_pin_set_says_so_rather_than_passing(self):
        """Unlike the portal check, the API never waves a PIN-less wallet through."""
        self.client.force_login(self.ada)
        data = self.post({"pin": "1234"}).json()
        self.assertEqual((data["ok"], data["no_pin"]), (False, True))

    def test_the_right_pin_returns_a_token_for_this_member(self):
        wallet_pin.set_wallet_pin(self.ada, "4821")
        self.client.force_login(self.ada)
        data = self.post({"pin": "4821"}).json()
        self.assertTrue(data["ok"])
        self.assertTrue(wallet_pin.verify_pin_token(data["pin_token"], self.ada))

    def test_a_wrong_pin_returns_no_token(self):
        wallet_pin.set_wallet_pin(self.ada, "4821")
        self.client.force_login(self.ada)
        data = self.post({"pin": "9999"}).json()
        self.assertEqual(data, {"ok": False, "error": "Incorrect PIN."})
        self.assertEqual(wallet_pin._PIN_TOKENS, {})


class WalletReadApiTests(TestCase):
    def setUp(self):
        self.ada = User.objects.create_user("ada", password="pw")
        self.bob = User.objects.create_user("bob", password="pw")
        self.asset = make_asset(unit_name="WAL", decimals=2, active=True)

    def account(self, user, code):
        return LedgerAccount.objects.create(code=code, name=code, account_type=AccountType.USER,
                                            user=user, active=True)

    def test_movements_need_a_sign_in(self):
        self.assertEqual(self.client.get("/assets/api/wallet/movements/").status_code, 401)

    def test_movements_are_the_members_own_newest_first_with_their_direction(self):
        from toto.assets.models import LedgerTransaction, TransactionType

        mine, theirs = self.account(self.ada, "ADA-W"), self.account(self.bob, "BOB-W")
        for n, (account, amount) in enumerate(((mine, 500), (mine, -200), (theirs, 900))):
            tx = LedgerTransaction.objects.create(reference=f"wal-{n}",
                                                  transaction_type=TransactionType.ADJUSTMENT)
            LedgerEntry.objects.create(transaction=tx, account=account, asset=self.asset,
                                       amount_base_units=amount)
        self.client.force_login(self.ada)
        rows = self.client.get("/assets/api/wallet/movements/").json()["movements"]
        self.assertEqual([r["transaction_reference"] for r in rows], ["wal-1", "wal-0"])
        self.assertEqual([r["is_credit"] for r in rows], [False, True])
        self.assertEqual(rows[0]["amount_display"], "-2.00")

    def test_a_rated_but_unposted_charge_is_pending_and_totalled(self):
        from toto.tariffs.models import BillingMetric, Tariff, TariffItem, TariffStatus, UsageRecord
        from toto.tariffs.services import rate_usage_record

        payer = self.account(self.ada, "ADA-PAY")
        revenue = LedgerAccount.objects.create(code="WAL-REV", name="rev",
                                               account_type=AccountType.SYSTEM, active=True)
        tariff = Tariff.objects.create(name="Wallet", code="WAL-T", status=TariffStatus.ACTIVE)
        metric = BillingMetric.objects.create(code="wal.op", label="wal.op", active=True)
        TariffItem.objects.create(tariff=tariff, name="op", metric=metric, charged_asset=self.asset,
                                  price_per_unit_display=Decimal("1.25"), receiving_account=revenue)
        for quantity in (1, 2):
            record = UsageRecord.objects.create(tariff=tariff, payer_account=payer,
                                                metric_code="wal.op", quantity=Decimal(quantity),
                                                unit="", occurred_at=timezone.now())
            rate_usage_record(record)
        self.client.force_login(self.ada)
        data = self.client.get("/assets/api/wallet/summary/").json()
        self.assertEqual(sorted(c["amount_display"] for c in data["pending_charges"]),
                         ["1.25", "2.50"])
        self.assertEqual(Decimal(data["totals"]["total_pending_by_asset"]["WAL"]), Decimal("3.75"))
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get("/assets/api/wallet/summary/").json()["pending_charges"], [])

    def test_the_summary_lists_holdings_of_active_accounts_only(self):
        live = self.account(self.ada, "ADA-LIVE")
        dead = self.account(self.ada, "ADA-DEAD")
        LedgerAccount.objects.filter(pk=dead.pk).update(active=False)
        AssetHolding.objects.create(account=live, asset=self.asset, balance_base_units=1234)
        self.client.force_login(self.ada)
        accounts = {a["code"]: a for a in self.client.get("/assets/api/wallet/summary/").json()["accounts"]}
        self.assertNotIn("ADA-DEAD", accounts)
        self.assertEqual(accounts["ADA-LIVE"]["holdings"][0]["balance_display"], "12.34")
