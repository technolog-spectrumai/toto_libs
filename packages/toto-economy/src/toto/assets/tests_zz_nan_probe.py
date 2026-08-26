"""TEMP audit probe: non-finite Decimal literals in the hourly-amount field."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.assets.models import Asset, Faucet, FaucetMember
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class NonFiniteAmountTests(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.staff = User.objects.create_user("staff", password="pw", is_staff=True)
        self.ada = User.objects.create_user("ada", password="pw")
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)
        self.client = Client(raise_request_exception=False)
        self.client.force_login(self.staff)

    def post(self, raw):
        return self.client.post(
            reverse("assets:faucet_member_add", args=[self.faucet.pk]),
            {"username": "ada", "amount_per_hour": raw})

    def check(self, raw):
        response = self.post(raw)
        self.assertNotEqual(
            response.status_code, 500,
            f"{raw!r} produced an unhandled 500 on the faucet member form")
        self.assertEqual(response.status_code, 302, f"{raw!r} -> unexpected")
        self.assertFalse(
            FaucetMember.objects.exists(),
            f"{raw!r} wrote a membership row")

    def test_nan(self):
        self.check("nan")

    def test_infinity(self):
        self.check("Infinity")

    def test_snan(self):
        self.check("snan")

    def test_negative_infinity(self):
        self.check("-Infinity")

    def test_a_plain_word_is_still_refused_cleanly(self):
        self.check("lots")
