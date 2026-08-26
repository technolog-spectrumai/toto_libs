"""TEMPORARY audit probes. Delete after the review."""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone

from toto.assets.models import (Asset, Faucet, FaucetMember, FaucetPayout,
                                FaucetPayoutStatus, FaucetRun, LedgerTransaction,
                                AssetHolding)
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
        self.faucet = Faucet.objects.create(name="Stipends", asset=self.mana,
                                            active=True)

    def add(self, username, amount):
        u = User.objects.create_user(username, password="pw")
        return FaucetMember.objects.create(faucet=self.faucet, user=u,
                                           amount_per_hour=Decimal(amount))

    def txs(self):
        return LedgerTransaction.objects.filter(reference__startswith="faucet-")

    # ---------------------------------------------------------------- probe 1
    def test_probe_huge_rate_kills_the_whole_run(self):
        """A 12-digit hourly rate: to_base_units -> 10**21, bigger than a
        BigIntegerField. What does the claim INSERT raise, and who else is
        still paid?"""
        self.add("aaa", "999999999999")      # 12 digits: the most the column takes
        self.add("zzz", "2")
        try:
            report = faucets.run_hour()
        except Exception as exc:
            print("PROBE1 run_hour RAISED", type(exc).__mro__[:4], exc)
            print("PROBE1 payouts:", list(FaucetPayout.objects.values_list(
                "member__user__username", "status")))
            print("PROBE1 txs:", self.txs().count())
            print("PROBE1 runs:", list(FaucetRun.objects.values_list(
                "period_label", "paid", "skipped", "failed", "finished_at")))
            return
        print("PROBE1 no raise:", report, list(FaucetPayout.objects.values_list(
            "member__user__username", "status", "amount_base_units")))

    # ---------------------------------------------------------------- probe 2
    def test_probe_dust_rate_fails_forever(self):
        self.add("dust", "0.0000000001")       # < 1 base unit at 9 decimals
        r1 = faucets.run_hour()
        p = FaucetPayout.objects.get()
        print("PROBE2", r1, p.status, repr(p.detail), p.amount_base_units)
        r2 = faucets.run_hour(at=timezone.now() + timezone.timedelta(hours=1))
        print("PROBE2 next hour", r2, [ (x.status, x.detail) for x in FaucetPayout.objects.all()])

    # ---------------------------------------------------------------- probe 3
    def test_probe_deleted_payout_row_is_replayed(self):
        m = self.add("ada", "2")
        faucets.run_hour()
        before = self.txs().count()
        bal_before = AssetHolding.objects.get(
            asset=self.mana, account__code=f"user-prepaid-{m.user.pk}").balance_base_units
        FaucetPayout.objects.all().delete()
        report = faucets.run_hour()
        bal_after = AssetHolding.objects.get(
            asset=self.mana, account__code=f"user-prepaid-{m.user.pk}").balance_base_units
        print("PROBE3", report, "txs", before, "->", self.txs().count(),
              "balance", bal_before, "->", bal_after,
              [(x.status, x.detail, x.transaction_id) for x in FaucetPayout.objects.all()])

    def test_probe_deleted_payout_row_after_rate_change(self):
        m = self.add("ada", "2")
        faucets.run_hour()
        bal_before = AssetHolding.objects.get(
            asset=self.mana, account__code=f"user-prepaid-{m.user.pk}").balance_base_units
        FaucetPayout.objects.all().delete()
        m.amount_per_hour = Decimal("5")
        m.save(update_fields=["amount_per_hour"])
        report = faucets.run_hour()
        bal_after = AssetHolding.objects.get(
            asset=self.mana, account__code=f"user-prepaid-{m.user.pk}").balance_base_units
        print("PROBE4", report, "balance", bal_before, "->", bal_after,
              [(x.status, x.detail) for x in FaucetPayout.objects.all()])

    # ---------------------------------------------------------------- probe 5
    def test_probe_two_faucets_same_asset(self):
        u = User.objects.create_user("dbl", password="pw")
        f2 = Faucet.objects.create(name="Second", asset=self.mana, active=True)
        FaucetMember.objects.create(faucet=self.faucet, user=u, amount_per_hour=Decimal("2"))
        FaucetMember.objects.create(faucet=f2, user=u, amount_per_hour=Decimal("3"))
        report = faucets.run_hour()
        bal = AssetHolding.objects.get(
            asset=self.mana, account__code=f"user-prepaid-{u.pk}").balance_base_units
        print("PROBE5", report, "balance", bal)
