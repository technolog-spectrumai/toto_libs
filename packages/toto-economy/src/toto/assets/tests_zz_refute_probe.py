"""Refutation probe for the 'deleted membership re-opens a paid hour' claim."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.assets.models import (Asset, AssetHolding, Faucet, FaucetMember,
                                FaucetPayout, LedgerTransaction)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class RefuteProbe(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.ada = User.objects.create_user("ada", password="pw")
        self.staff = User.objects.create_user("boss", password="pw", is_staff=True)
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)

    def bal(self, user):
        h = AssetHolding.objects.filter(
            asset=self.mana, account__code=f"user-prepaid-{user.pk}").first()
        return h.balance_base_units if h else 0

    def txs(self):
        return LedgerTransaction.objects.filter(reference__startswith="faucet-")

    # --- the path the app actually offers ---------------------------------
    def test_ui_remove_then_readd_in_the_same_hour_pays_once(self):
        m = FaucetMember.objects.create(faucet=self.faucet, user=self.ada,
                                        amount_per_hour=Decimal("2"))
        faucets.run_hour()
        after_first = self.bal(self.ada)
        self.client.force_login(self.staff)

        r = self.client.post(reverse("assets:faucet_member_remove", args=[m.pk]))
        print("REMOVE status", r.status_code,
              "rows left:", FaucetMember.objects.count(),
              "active:", list(FaucetMember.objects.values_list("pk", "active")))
        r = self.client.post(reverse("assets:faucet_member_add", args=[self.faucet.pk]),
                             {"username": "ada", "amount_per_hour": "2"})
        print("READD status", r.status_code,
              "rows now:", list(FaucetMember.objects.values_list("pk", "active")))

        rep = faucets.run_hour()
        print("UI PATH report:", rep, "txs:", self.txs().count(),
              "balance:", after_first, "->", self.bal(self.ada),
              "payouts:", FaucetPayout.objects.count())
        self.assertEqual(self.txs().count(), 1)
        self.assertEqual(self.bal(self.ada), after_first)
        self.assertEqual(FaucetPayout.objects.count(), 1)

    # --- the claim's own sequence: raw ORM delete --------------------------
    def test_hard_delete_and_recreate_mechanic(self):
        FaucetMember.objects.create(faucet=self.faucet, user=self.ada,
                                    amount_per_hour=Decimal("2"))
        faucets.run_hour()
        first = self.bal(self.ada)
        self.faucet.delete()
        f2 = Faucet.objects.create(name="Stipends", asset=self.mana, active=True)
        FaucetMember.objects.create(faucet=f2, user=self.ada,
                                    amount_per_hour=Decimal("2"))
        rep = faucets.run_hour()
        print("HARD DELETE report:", rep, "txs:", self.txs().count(),
              "refs:", list(self.txs().values_list("reference", flat=True)),
              "balance:", first, "->", self.bal(self.ada))

    # --- would a delete of the payout row alone do it? ---------------------
    def test_deleting_only_the_payout_row(self):
        FaucetMember.objects.create(faucet=self.faucet, user=self.ada,
                                    amount_per_hour=Decimal("2"))
        faucets.run_hour()
        first = self.bal(self.ada)
        FaucetPayout.objects.all().delete()
        rep = faucets.run_hour()
        print("PAYOUT-ONLY DELETE report:", rep, "txs:", self.txs().count(),
              "balance:", first, "->", self.bal(self.ada))
