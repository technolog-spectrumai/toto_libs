"""The mint tab over HTTP: who may press the three buttons, and what each
refusal looks like.

Every refusal a staff member can provoke is a message on the index page,
never a 500; every refusal a non-staff member provokes is a 403. The economy
desk gate on this host's /mint/ prefix is switched off here (it is the host's
product decision, tested in zenobia's own suites) so these assert the app's
own contract.
"""

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import override_settings
from django.urls import reverse

from toto.assets.models import AccountType, Asset, LedgerAccount
from toto.assets.queries import get_asset_balance
from toto.assets.services.assets import engrave_currency
from toto.assets.testing import TEST_ISSUER_KEY
from toto.assets.testing import LedgerTestCase as TestCase
from toto.assets.testing import make_asset
from toto.mint.history import supply
from toto.mint.models import CurrencyMintEvent, IssuanceRecord

User = get_user_model()


def _platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _messages(response):
    return " ".join(str(m) for m in get_messages(response.wsgi_request))


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ECONOMY_STAFF_ONLY=False)
class DeskTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        _platform()
        cls.staff = User.objects.create_user("mintstaff", password="pw",
                                             is_staff=True)
        cls.member = User.objects.create_user("mintmember", password="pw")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def _engraved(self, unit="DSK", maximum="1000", minted=None):
        """An engraved currency with a reserve, minted up to ``minted``."""
        from toto.mint.services import mint

        reserve = LedgerAccount.objects.create(
            code=f"res-{unit.lower()}", name="Reserve",
            account_type=AccountType.RESERVE)
        asset = engrave_currency(name=f"{unit} coin", unit_name=unit,
                                 max_supply=Decimal(maximum), decimals=2)
        asset.reserve_account = reserve
        asset.save(update_fields=["reserve_account", "updated_at"])
        if minted:
            mint(asset=asset, amount=Decimal(minted), reason="opening")
        return asset


class IndexTests(DeskTestCase):
    def test_an_anonymous_visitor_is_sent_to_log_in(self):
        self.client.logout()
        response = self.client.get(reverse("mint:index"))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn(reverse("mint:index"), response["Location"].split("?")[0])

    def test_a_member_without_the_privilege_is_refused_with_403(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(reverse("mint:index")).status_code, 403)

    def test_a_superuser_who_is_not_staff_is_admitted(self):
        boss = User.objects.create_user("mintboss", password="pw",
                                        is_superuser=True)
        self.client.force_login(boss)
        self.assertEqual(self.client.get(reverse("mint:index")).status_code, 200)

    def test_staff_see_every_local_currency_and_a_sound_chain(self):
        asset = self._engraved("IDX", minted="250")
        response = self.client.get(reverse("mint:index"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_master"])
        rows = {row.asset.unit_name: row for row in response.context["currencies"]}
        self.assertEqual(rows["IDX"].supply_base_units, 25_000)
        self.assertEqual(rows["IDX"].unminted_base_units, 75_000)
        self.assertTrue(response.context["chain"]["ok"])
        self.assertEqual(list(response.context["events"])[0].asset, asset)

    def test_a_host_without_the_key_still_shows_the_page_but_says_so(self):
        with mock.patch("toto.assets.issuer.is_monetary_master",
                        return_value=False):
            response = self.client.get(reverse("mint:index"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_master"])


class EngraveTests(DeskTestCase):
    def _post(self, **over):
        data = {"name": "Desk coin", "unit_name": "dkc", "total_supply": "500",
                "decimals": "2", "code": "desk", "symbol": "¤",
                "reason": "The desk needs a coin."}
        data.update(over)
        return self.client.post(reverse("mint:issue"), data)

    def test_engraving_mints_the_whole_supply_and_records_who_and_why(self):
        response = self._post()
        self.assertRedirects(response, reverse("mint:index"),
                             fetch_redirect_response=False)
        asset = Asset.objects.get(unit_name="DKC")    # upper-cased
        self.assertEqual(asset.code, "DESK")          # and the short name too
        self.assertEqual(supply(asset), 50_000)
        record = IssuanceRecord.objects.get(asset=asset)
        self.assertEqual(record.actor, self.staff)
        self.assertEqual(record.reason, "The desk needs a coin.")
        self.assertIn("Engraved DKC", _messages(response))

    def test_a_supply_that_is_not_a_number_is_a_message_not_a_500(self):
        response = self._post(total_supply="lots")
        self.assertEqual(response.status_code, 302)
        self.assertIn("is not a number", _messages(response))
        self.assertFalse(Asset.objects.filter(unit_name="DKC").exists())

    def test_decimals_that_are_not_a_number_are_a_message_not_a_500(self):
        response = self._post(decimals="two")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(_messages(response))
        self.assertFalse(Asset.objects.filter(unit_name="DKC").exists())

    def test_a_missing_reason_is_refused_and_writes_nothing(self):
        response = self._post(reason="  ")
        self.assertIn("needs a reason", _messages(response))
        self.assertFalse(Asset.objects.filter(unit_name="DKC").exists())
        self.assertFalse(IssuanceRecord.objects.exists())

    def test_a_short_name_that_is_not_four_letters_is_a_message_not_a_500(self):
        for code in ("dk", "DESKS", "DE5K"):
            with self.subTest(code=code):
                response = self._post(code=code)
                self.assertEqual(response.status_code, 302)
                self.assertIn("four capital letters", _messages(response))
                self.assertFalse(Asset.objects.filter(unit_name="DKC").exists())

    def test_a_blank_short_name_is_derived_from_the_ticker(self):
        self._post(code="")
        self.assertEqual(Asset.objects.get(unit_name="DKC").code, "DKCX")

    def test_engraving_a_ticker_twice_is_refused_and_keeps_the_first(self):
        self._post()
        # A blank short name, so it is the TICKER that collides and not the code.
        response = self._post(name="Impostor", reason="again", code="")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(_messages(response))
        self.assertEqual(Asset.objects.filter(unit_name="DKC").count(), 1)
        self.assertEqual(Asset.objects.get(unit_name="DKC").name, "Desk coin")

    def test_engraving_needs_a_post(self):
        self.assertEqual(self.client.get(reverse("mint:issue")).status_code, 405)

    def test_a_member_cannot_engrave(self):
        self.client.force_login(self.member)
        self.assertEqual(self._post().status_code, 403)
        self.assertFalse(Asset.objects.filter(unit_name="DKC").exists())

    def test_a_host_without_the_key_refuses_before_anything_is_written(self):
        with mock.patch("toto.assets.issuer.is_monetary_master",
                        return_value=False):
            response = self._post()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Asset.objects.filter(unit_name="DKC").exists())


class MintAndBurnTests(DeskTestCase):
    def test_minting_adds_to_the_reserve_and_names_the_event(self):
        asset = self._engraved("MNT", minted="100")
        response = self.client.post(
            reverse("mint:mint_units", args=[asset.pk]),
            {"amount": "10", "reason": "top up"})
        self.assertRedirects(response, reverse("mint:index"),
                             fetch_redirect_response=False)
        self.assertEqual(supply(asset), 11_000)
        self.assertEqual(get_asset_balance(asset, asset.reserve_account), 11_000)
        event = CurrencyMintEvent.objects.filter(asset=asset).last()
        self.assertEqual((event.kind, event.amount_base_units, event.actor),
                         ("mint", 1000, self.staff))
        self.assertIn("Minted 1000 base units of MNT", _messages(response))

    def test_minting_past_the_engraved_maximum_is_a_message(self):
        asset = self._engraved("CAP", maximum="100", minted="100")
        response = self.client.post(
            reverse("mint:mint_units", args=[asset.pk]),
            {"amount": "0.01", "reason": "one more"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("maximum", _messages(response))
        self.assertEqual(supply(asset), 10_000)

    def test_a_blank_amount_is_refused_as_not_positive(self):
        asset = self._engraved("BLK", minted="1")
        response = self.client.post(
            reverse("mint:mint_units", args=[asset.pk]),
            {"amount": "", "reason": "nothing"})
        self.assertIn("positive", _messages(response))
        self.assertEqual(supply(asset), 100)

    def test_burning_destroys_reserve_units(self):
        asset = self._engraved("BRN", minted="100")
        response = self.client.post(
            reverse("mint:burn_units", args=[asset.pk]),
            {"amount": "40", "reason": "too many"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(supply(asset), 6_000)
        self.assertIn("Burned 4000 base units of BRN", _messages(response))

    def test_burning_more_than_the_reserve_holds_is_a_message(self):
        asset = self._engraved("BRX", minted="10")
        response = self.client.post(
            reverse("mint:burn_units", args=[asset.pk]),
            {"amount": "11", "reason": "too far"})
        self.assertIn("cannot be burned", _messages(response))
        self.assertEqual(supply(asset), 1_000)

    def test_a_mirror_can_be_neither_minted_nor_burned(self):
        mirror = make_asset(unit_name="MRR", is_mirror=True)
        for name in ("mint:mint_units", "mint:burn_units"):
            response = self.client.post(reverse(name, args=[mirror.pk]),
                                        {"amount": "1", "reason": "no"})
            self.assertEqual(response.status_code, 404)
        self.assertFalse(CurrencyMintEvent.objects.filter(asset=mirror).exists())

    def test_a_member_can_neither_mint_nor_burn(self):
        asset = self._engraved("MBR", minted="10")
        self.client.force_login(self.member)
        for name in ("mint:mint_units", "mint:burn_units"):
            response = self.client.post(reverse(name, args=[asset.pk]),
                                        {"amount": "1", "reason": "sneaky"})
            self.assertEqual(response.status_code, 403)
        self.assertEqual(supply(asset), 1_000)

    def test_without_the_key_minting_and_burning_are_403(self):
        asset = self._engraved("NOK", minted="10")
        with mock.patch("toto.assets.issuer.is_monetary_master",
                        return_value=False):
            for name in ("mint:mint_units", "mint:burn_units"):
                response = self.client.post(reverse(name, args=[asset.pk]),
                                            {"amount": "1", "reason": "x"})
                self.assertEqual(response.status_code, 403)
        self.assertEqual(supply(asset), 1_000)
