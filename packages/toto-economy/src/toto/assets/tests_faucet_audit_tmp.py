"""TEMPORARY audit probes. Delete after the review."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetRun, LedgerTransaction)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class Probe(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.faucet = Faucet.objects.create(name="A-stipends", asset=self.mana,
                                            active=True)

    def add(self, username, amount, faucet=None):
        u = User.objects.create_user(username, password="pw")
        return FaucetMember.objects.create(faucet=faucet or self.faucet, user=u,
                                           amount_per_hour=Decimal(amount))

    def txs(self):
        return LedgerTransaction.objects.filter(reference__startswith="faucet-")

    def test_ten_billion_an_hour_is_accepted_by_the_staff_form(self):
        staff = User.objects.create_user("staff", password="pw", is_staff=True)
        User.objects.create_user("greedy", password="pw")
        self.client.force_login(staff)
        r = self.client.post(
            reverse("assets:faucet_member_add", args=[self.faucet.pk]),
            {"username": "greedy", "amount_per_hour": "10000000000"}, follow=True)
        print("PROBE-FORM status", r.status_code,
              "member?", FaucetMember.objects.filter(user__username="greedy")
              .values_list("amount_per_hour", flat=True).first())

    def test_one_bad_rate_stops_everybody_every_hour(self):
        bad = self.add("aaa", "10000000000")          # 1e10 MANA/h -> 1e19 base units
        self.add("zzz", "2")
        other = Faucet.objects.create(name="Z-other", asset=self.mana, active=True)
        self.add("qqq", "1", faucet=other)

        for hour in range(3):
            at = timezone.now() + timezone.timedelta(hours=hour)
            try:
                faucets.run_hour(at=at)
                print(f"PROBE-BLAST hour{hour}: no raise")
            except Exception as exc:
                print(f"PROBE-BLAST hour{hour}: {type(exc).__name__}: {exc}")
        print("PROBE-BLAST payouts:", list(FaucetPayout.objects.values_list(
            "member__user__username", "status")))
        print("PROBE-BLAST money moved:", self.txs().count())
        print("PROBE-BLAST run rows:", list(FaucetRun.objects.values_list(
            "period_label", "paid", "skipped", "failed", "finished_at")))
        print("PROBE-BLAST bad member rate:", bad.amount_per_hour)

    def test_the_task_itself_explodes(self):
        from toto.assets.tasks import run_faucet_hour
        self.add("aaa", "10000000000")
        try:
            run_faucet_hour()
            print("PROBE-TASK: returned")
        except Exception as exc:
            print("PROBE-TASK raised", type(exc).__name__, exc)
