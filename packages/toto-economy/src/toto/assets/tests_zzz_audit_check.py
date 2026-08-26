"""Independent check of the claimed BigInteger-overflow defect."""
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
class OverflowCheck(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)

    def member(self, name, amount):
        u = User.objects.create_user(name, password="pw")
        return FaucetMember.objects.create(faucet=self.faucet, user=u,
                                           amount_per_hour=Decimal(amount))

    def txs(self, name):
        return LedgerTransaction.objects.filter(
            reference__startswith="faucet-").filter(description__isnull=False).count()

    def test_the_view_accepts_an_overflowing_rate(self):
        staff = User.objects.create_user("boss", password="pw", is_staff=True)
        User.objects.create_user("mmm", password="pw")
        self.client.force_login(staff)
        resp = self.client.post(
            reverse("assets:faucet_member_add", args=[self.faucet.pk]),
            {"username": "mmm", "amount_per_hour": "10000000000"})
        print("\n  view status:", resp.status_code)
        m = FaucetMember.objects.filter(user__username="mmm").first()
        print("  stored rate:", None if m is None else m.amount_per_hour)
        self.assertIsNotNone(m, "view rejected the rate")

    def test_a_bad_rate_does_not_stop_the_rest_of_the_roster(self):
        aaa = self.member("aaa", "1")
        bad = self.member("mmm", "10000000000")     # 1e10 MANA/h, 9 decimals
        zzz = self.member("zzz", "1")
        print("\n  order:", list(faucets.due_members().values_list(
            "user__username", flat=True)))

        now = timezone.now()
        for h in range(3):
            try:
                rep = faucets.run_hour(at=now + timezone.timedelta(hours=h))
                print(f"  hour+{h}: {rep} failures={rep.failures}")
            except Exception as exc:                        # noqa: BLE001
                print(f"  hour+{h}: RAISED {type(exc).__module__}."
                      f"{type(exc).__name__}: {exc}")

        for who in ("aaa", "zzz"):
            n = LedgerTransaction.objects.filter(
                reference__startswith="faucet-",
                reference__contains=str(FaucetMember.objects.get(
                    user__username=who).pk)).count()
            print(f"  {who} transactions:", n)
        print("  payouts:", list(FaucetPayout.objects.values_list(
            "member__user__username", "status", "amount_base_units")))
        print("  runs:", list(FaucetRun.objects.values_list(
            "period_label", "paid", "skipped", "failed", "finished_at")))

        paid_zzz = FaucetPayout.objects.filter(
            member__user__username="zzz", status="paid").count()
        self.assertEqual(paid_zzz, 3, "member sorted after the bad one lost hours")
