"""Verification probe: does an out-of-range amount_base_units escape pay_member?"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetPayoutStatus, FaucetRun, LedgerTransaction)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class BigIntCheck(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.mana = Asset.objects.get(unit_name="MANA")
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)

    def member(self, name, amount, faucet=None):
        u = User.objects.create_user(name, password="pw")
        return FaucetMember.objects.create(faucet=faucet or self.faucet, user=u,
                                           amount_per_hour=Decimal(amount))

    def test_view_accepts_the_absurd_rate(self):
        """Reach FaucetMember through the staff view, not the ORM."""
        from django.test import Client
        staff = User.objects.create_user("boss", password="pw", is_staff=True,
                                         is_superuser=True)
        c = Client()
        c.force_login(staff)
        User.objects.create_user("bob0", password="pw")
        r = c.post(f"/assets/faucets/{self.faucet.pk}/members/add/",
                   {"username": "bob0", "amount_per_hour": "10000000000"})
        print("\n  view status:", r.status_code)
        m = FaucetMember.objects.filter(user__username="bob0").first()
        print("  stored member:", m and m.amount_per_hour)
        self.assertIsNone(m, "the view stored an unbounded hourly rate")

    def test_one_huge_rate_does_not_take_down_the_run(self):
        good = self.member("alice", "2")
        bad = self.member("bob0", "10000000000")     # 1e10 MANA/h, 9 decimals
        other = self.member("zoe", "3")
        try:
            report = faucets.run_hour()
        except Exception as exc:                     # noqa: BLE001
            self.fail(f"run_hour raised {type(exc).__module__}."
                      f"{type(exc).__name__}: {exc}")
        print("\n  report:", report, report.failures)
        self.assertEqual(
            set(FaucetPayout.objects.filter(status=FaucetPayoutStatus.PAID)
                .values_list("member__user__username", flat=True)),
            {"alice", "zoe"})

    def test_eighteen_decimal_asset_ordinary_rate(self):
        """decimals up to 19 is allowed by Asset.clean and the create view."""
        from toto.assets.services import assets as asset_services
        token = Asset.objects.filter(unit_name="MANA").first()
        # build a fresh 18-decimal asset the way the app does
        from toto.assets.views import _engrave_new_asset
        try:
            new = _engrave_new_asset(name="Token", unit_name="TOK",
                                     total_supply=Decimal("1000000"),
                                     decimals=18, user=None)
        except TypeError as exc:
            print("\n  _engrave_new_asset signature:", exc)
            return
        print("\n  made:", new)

    def test_next_hours_also_die(self):
        self.member("alice", "2")
        self.member("bob0", "10000000000")
        crashes = 0
        for h in range(3):
            try:
                faucets.run_hour(at=timezone.now() + timezone.timedelta(hours=h))
            except Exception as exc:                 # noqa: BLE001
                crashes += 1
                print(f"\n  hour+{h} crashed: {type(exc).__name__}: {exc}")
        print("  runs:", list(FaucetRun.objects.values_list(
            "period_label", "paid", "skipped", "failed", "finished_at")))
        print("  txs:", LedgerTransaction.objects.filter(
            reference__startswith="faucet-").count())
        self.assertEqual(crashes, 0, "every hourly run crashed")
