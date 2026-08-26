from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.assets.models import (Asset, AssetHolding, Faucet, FaucetMember,
                                FaucetPayout, FaucetPayoutStatus, FaucetRun,
                                LedgerTransaction)
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

    def reserve_holding(self, asset=None):
        asset = asset or self.mana
        return AssetHolding.objects.get(asset=asset,
                                        account=asset.reserve_account)

    def set_reserve(self, display, asset=None):
        asset = asset or self.mana
        h = self.reserve_holding(asset)
        h.balance_base_units = int(Decimal(display) * 10 ** asset.decimals)
        h.save(update_fields=["balance_base_units"])

    def member(self, name, amount, faucet=None):
        u = User.objects.create_user(name, password="pw")
        return FaucetMember.objects.create(faucet=faucet or self.faucet, user=u,
                                           amount_per_hour=Decimal(amount))

    # ------------------------------------------------------------------ #
    def test_probe_reserve_drain_midrun(self):
        for n, a in [("ann", "2"), ("bea", "2"), ("cid", "2")]:
            self.member(n, a)
        self.set_reserve("5")
        report = faucets.run_hour()
        print("\nDRAIN report:", report, "failures:", report.failures)
        print("DRAIN reserve after:", self.reserve_holding().balance_base_units)
        for p in FaucetPayout.objects.select_related("member__user").order_by("id"):
            print("  payout", p.member.user.username, p.status,
                  p.amount_base_units, repr(p.detail))
        run = FaucetRun.objects.get()
        print("DRAIN run:", run.paid, run.skipped, run.failed, run.finished_at)

    def test_probe_reserve_never_negative(self):
        self.member("ann", "2")
        self.set_reserve("1")
        faucets.run_hour()
        print("\nNEG reserve:", self.reserve_holding().balance_base_units)
        self.assertGreaterEqual(self.reserve_holding().balance_base_units, 0)

    def test_probe_sub_quantum_rate(self):
        self.member("ann", "0.0000000001")   # MANA has 9 decimals
        r = faucets.run_hour()
        p = FaucetPayout.objects.get()
        print("\nSUBQ:", r, p.status, p.amount_base_units, repr(p.detail))

    def test_probe_bigint_overflow_kills_the_run(self):
        """An 18-decimals currency (allowed: 0..19) paying 10/hour."""
        from toto.mint.services import create_currency
        from toto.assets.models import LedgerAccount, AccountType

        reserve = LedgerAccount.objects.create(
            code="RES-WEI", name="wei reserve",
            account_type=AccountType.RESERVE, active=True)
        wei = create_currency(name="Wei", unit_name="WEI",
                              total_supply=Decimal("1000"), decimals=18,
                              reserve_account=reserve, reference="mint-wei",
                              description="x")
        wei.reserve_account = reserve
        wei.save(update_fields=["reserve_account"])
        big = Faucet.objects.create(name="AAA-big", asset=wei, active=True)
        self.member("zoe", "10", faucet=big)     # 10 * 10**18 = 1e19 > bigint
        self.member("ann", "2")                  # ordinary MANA member

        try:
            report = faucets.run_hour()
            print("\nOVERFLOW: no crash ->", report, report.failures)
        except Exception as exc:
            print("\nOVERFLOW CRASHED:", type(exc).__module__ + "." + type(exc).__name__, exc)
            print("  payouts:", list(FaucetPayout.objects.values_list(
                "member__user__username", "status", "amount_base_units")))
            print("  runs:", list(FaucetRun.objects.values_list(
                "period_label", "paid", "skipped", "failed", "finished_at")))
            print("  ledger:", list(LedgerTransaction.objects.filter(
                reference__startswith="faucet-").values_list("reference", flat=True)))
