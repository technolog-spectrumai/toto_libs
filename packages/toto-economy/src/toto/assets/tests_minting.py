"""Engraving a currency from the Assets UI, and every way it can be refused.

Every case here answered HTTP 500 before 8/2026. The view caught exactly
``(ValidationError, InvalidOperation)`` and four ordinary things are neither:

* a blank or non-numeric decimals field — ``ValueError`` out of ``int()``;
* a unit name that already exists — ``IntegrityError`` on the unique index;
* a host with no ``MONETARY_ISSUER_KEY`` — ``NotTheMaster``, which is the state
  every host is in until an operator sets one, and therefore the likeliest 500
  of the four;
* a second identical submit, which raced the first into the same constraint.

It also wrote the reserve account BEFORE calling the mint and outside any
transaction, so each of those failures left an orphaned ``RES-<UNIT>`` account
behind. ``ingress_assets`` has carried a note about that exact shape since it
was written; this is the same lesson, applied to the door people click.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.test import TestCase as PlainTestCase
from django.urls import reverse

from toto.assets.models import AccountType, Asset, LedgerAccount
from toto.assets.testing import TEST_ISSUER_KEY
from toto.assets.testing import LedgerTestCase as TestCase
from toto.core.models import Platform

User = get_user_model()


def _platform():
    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


#: The issuer key is supplied HERE rather than read from the environment.
#: `LedgerTestCase` mints an issuer in setUpTestData, which needs
#: MONETARY_ISSUER_KEY — and zenobia defaults it to "" because a host is not a
#: monetary master until an operator makes it one. A suite that relied on the
#: environment passed locally and failed in the gate, which sets no such key.
@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY)
class MintingTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()          # IssuerFixtureMixin: this host may issue
        _platform()
        cls.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        cls.punter = User.objects.create_user("punter", password="pw")

    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def post(self, **over):
        data = {"name": "Mana", "unit_name": "MANA", "total_supply": "1000",
                "decimals": "6", "description": "", "reserve_choice": "auto"}
        data.update(over)
        return self.client.post(reverse("assets:asset_create"), data)

    def assertRefused(self, response, *, needle=""):
        """A refusal is a rendered page with a message — never a 500."""
        self.assertEqual(response.status_code, 200)
        text = " ".join(str(m) for m in response.context["messages"])
        if needle:
            self.assertIn(needle, text.lower(), text)
        return text


class SuccessTests(MintingTestCase):

    def test_it_engraves_the_currency_and_mints_the_opening_supply(self):
        response = self.post()
        asset = Asset.objects.get(unit_name="MANA")
        self.assertRedirects(
            response, asset.get_absolute_url())
        self.assertEqual(asset.name, "Mana")
        self.assertEqual(asset.decimals, 6)

    def test_the_reserve_account_is_created_and_attached(self):
        self.post()
        asset = Asset.objects.get(unit_name="MANA")
        self.assertIsNotNone(asset.reserve_account_id)
        self.assertEqual(asset.reserve_account.code, "RES-MANA")
        self.assertEqual(asset.reserve_account.account_type, AccountType.RESERVE)

    def test_an_existing_reserve_account_can_be_chosen_instead(self):
        chosen = LedgerAccount.objects.create(
            code="house-reserve", name="House", account_type=AccountType.RESERVE,
            active=True)
        self.post(reserve_choice="existing", reserve_account=str(chosen.pk))
        self.assertEqual(
            Asset.objects.get(unit_name="MANA").reserve_account_id, chosen.pk)

    def test_the_unit_name_is_upper_cased(self):
        self.post(unit_name="mana")
        self.assertTrue(Asset.objects.filter(unit_name="MANA").exists())


class InvalidAmountTests(MintingTestCase):

    def test_a_blank_decimals_field_is_refused_not_a_500(self):
        self.assertRefused(self.post(decimals=""), needle="whole number")
        self.assertFalse(Asset.objects.filter(unit_name="MANA").exists())

    def test_a_non_numeric_decimals_field_is_refused(self):
        self.assertRefused(self.post(decimals="abc"), needle="whole number")

    def test_decimals_out_of_range_is_refused(self):
        self.assertRefused(self.post(decimals="42"), needle="between 0 and 19")

    def test_a_non_numeric_supply_is_refused(self):
        self.assertRefused(self.post(total_supply="lots"), needle="not a number")

    def test_a_blank_supply_is_refused(self):
        self.assertRefused(self.post(total_supply=""), needle="not a number")

    def test_a_zero_supply_is_refused(self):
        self.assertRefused(self.post(total_supply="0"), needle="greater than zero")

    def test_a_negative_supply_is_refused(self):
        self.assertRefused(self.post(total_supply="-5"), needle="greater than zero")
        self.assertFalse(Asset.objects.exists())

    def test_every_problem_is_reported_at_once(self):
        """Not one per round trip: a form that reveals its objections one at a
        time is a form somebody submits five times."""
        text = self.assertRefused(self.post(name="", total_supply="x", decimals="y"))
        self.assertIn("name is required", text.lower())
        self.assertIn("not a number", text.lower())
        self.assertIn("whole number", text.lower())


class MissingFieldTests(MintingTestCase):

    def test_a_missing_name_is_refused(self):
        self.assertRefused(self.post(name=""), needle="name is required")

    def test_a_missing_unit_name_is_refused(self):
        self.assertRefused(self.post(unit_name=""), needle="unit name is required")

    def test_a_unit_name_with_punctuation_is_refused(self):
        self.assertRefused(self.post(unit_name="MA-NA"), needle="letters and digits")

    def test_a_missing_reserve_account_is_refused(self):
        self.assertRefused(self.post(reserve_choice="existing", reserve_account=""),
                           needle="choose an existing reserve")

    def test_a_reserve_account_that_does_not_exist_is_refused(self):
        self.assertRefused(
            self.post(reserve_choice="existing", reserve_account="99999"),
            needle="choose an existing reserve")

    def test_an_inactive_reserve_account_is_refused(self):
        dead = LedgerAccount.objects.create(
            code="old-reserve", name="Old", account_type=AccountType.RESERVE,
            active=False)
        self.assertRefused(
            self.post(reserve_choice="existing", reserve_account=str(dead.pk)),
            needle="not active")


class DuplicateTests(MintingTestCase):

    def test_a_second_identical_submit_creates_nothing_and_says_why(self):
        """The double-click, which used to be an IntegrityError 500."""
        self.post()
        self.assertRefused(self.post(), needle="already exists")
        self.assertEqual(Asset.objects.filter(unit_name="MANA").count(), 1)

    def test_the_second_submit_leaves_the_first_asset_untouched(self):
        self.post(total_supply="1000")
        before = Asset.objects.get(unit_name="MANA").max_supply_base_units
        self.post(total_supply="9999")
        self.assertEqual(
            Asset.objects.get(unit_name="MANA").max_supply_base_units, before)


class PartialStateTests(MintingTestCase):

    def test_a_refused_engraving_leaves_no_reserve_account_behind(self):
        """The account used to be written first and outside any transaction, so
        every failure left an orphan `RES-<UNIT>` with no currency to name it."""
        self.post(decimals="abc")
        self.assertFalse(LedgerAccount.objects.filter(code="RES-MANA").exists())
        self.assertFalse(Asset.objects.filter(unit_name="MANA").exists())

    def test_a_refused_engraving_writes_no_asset(self):
        self.post(total_supply="-1")
        self.assertFalse(Asset.objects.exists())


class PermissionTests(MintingTestCase):

    def test_a_non_staff_user_is_forbidden(self):
        self.client.force_login(self.punter)
        self.assertEqual(self.post().status_code, 403)
        self.assertFalse(Asset.objects.exists())

    def test_a_non_staff_user_cannot_even_see_the_form(self):
        self.client.force_login(self.punter)
        self.assertEqual(
            self.client.get(reverse("assets:asset_create")).status_code, 403)


class NotTheMasterTests(PlainTestCase):
    """A host with no issuer key is the state EVERY host starts in.

    Deliberately a plain TestCase: `LedgerTestCase` mints an issuer for the test
    host, which is exactly the condition being excluded here. This is the 500
    an operator was most likely to meet, because it needed nothing to go wrong —
    only for `MONETARY_ISSUER_KEY` to be unset, which is its default.
    """

    def setUp(self):
        _platform()
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.client.force_login(self.staff)

    def test_it_refuses_in_words_rather_than_answering_500(self):
        response = self.client.post(reverse("assets:asset_create"), {
            "name": "Mana", "unit_name": "MANA", "total_supply": "1000",
            "decimals": "6", "description": "", "reserve_choice": "auto"})
        self.assertEqual(response.status_code, 200)
        text = " ".join(str(m) for m in response.context["messages"])
        self.assertIn("no monetary issuer key", text)

    def test_it_leaves_no_reserve_account_behind(self):
        self.client.post(reverse("assets:asset_create"), {
            "name": "Mana", "unit_name": "MANA", "total_supply": "1000",
            "decimals": "6", "description": "", "reserve_choice": "auto"})
        self.assertFalse(LedgerAccount.objects.filter(code="RES-MANA").exists())


class DistributeTests(MintingTestCase):
    """The sibling door, which caught `(ValidationError, Exception)` — that is
    `except Exception` with a longer spelling, and it reported a missing
    recipient as a distribution failure."""

    def setUp(self):
        super().setUp()
        self.post()
        self.asset = Asset.objects.get(unit_name="MANA")
        self.url = reverse("assets:asset_distribute", args=[self.asset.code])
        self.target = LedgerAccount.objects.create(
            code="alice", name="Alice", account_type=AccountType.USER, active=True)

    def messages_after(self, **data):
        response = self.client.post(self.url, data, follow=True)
        return " ".join(str(m) for m in response.context["messages"]).lower()

    def test_a_distribution_lands(self):
        text = self.messages_after(amount="10", recipient_account=str(self.target.pk))
        self.assertIn("distributed", text)

    def test_a_missing_recipient_is_refused_by_name(self):
        self.assertIn("choose an account",
                      self.messages_after(amount="10", recipient_account=""))

    def test_a_non_numeric_amount_is_refused(self):
        self.assertIn("not a number",
                      self.messages_after(amount="ten",
                                          recipient_account=str(self.target.pk)))

    def test_a_negative_amount_is_refused(self):
        self.assertIn("greater than zero",
                      self.messages_after(amount="-1",
                                          recipient_account=str(self.target.pk)))

    def test_a_non_staff_stranger_is_forbidden(self):
        self.client.force_login(self.punter)
        response = self.client.post(
            self.url, {"amount": "10", "recipient_account": str(self.target.pk)})
        self.assertEqual(response.status_code, 403)


class ShortNameTests(MintingTestCase):
    """The desk's "Short name" field (2026-09-30): four letters, the key of the
    asset's page; blank for one derived from the ticker."""

    def test_the_form_asks_for_it(self):
        body = self.client.get(reverse("assets:asset_create")).content.decode()
        self.assertIn('name="code"', body)

    def test_a_valid_short_name_is_kept_and_keys_the_page(self):
        response = self.post(code="ARCN")
        asset = Asset.objects.get(unit_name="MANA")
        self.assertEqual(asset.code, "ARCN")
        self.assertRedirects(response, reverse("assets:asset_detail", args=["ARCN"]))

    def test_lower_case_is_upper_cased(self):
        self.post(code=" arcn ")
        self.assertEqual(Asset.objects.get(unit_name="MANA").code, "ARCN")

    def test_a_blank_short_name_is_derived_from_the_ticker(self):
        self.post(unit_name="SPRITE", code="")
        self.assertEqual(Asset.objects.get(unit_name="SPRITE").code, "SPRI")

    def test_the_wrong_length_or_characters_are_refused_in_words(self):
        for code in ("ARC", "ARCNA", "AR1N", "AR-N", "AR N", "ÄRCN"):
            with self.subTest(code=code):
                self.assertRefused(self.post(code=code), needle="short name")
                self.assertFalse(Asset.objects.filter(unit_name="MANA").exists())
                self.assertFalse(LedgerAccount.objects.filter(code="RES-MANA").exists())

    def test_a_taken_short_name_is_refused_in_words(self):
        self.post(name="Arcana", unit_name="ARCANA", code="ARCN")
        text = self.assertRefused(self.post(code="arcn"), needle="is taken")
        self.assertIn("ARCN", text)
        self.assertFalse(Asset.objects.filter(unit_name="MANA").exists())
        self.assertEqual(Asset.objects.get(code="ARCN").unit_name, "ARCANA")
