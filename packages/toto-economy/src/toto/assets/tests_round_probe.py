"""Probe: what the HALF_EVEN claim actually does. Temporary."""
from decimal import Decimal, ROUND_DOWN

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from toto.assets.models import (Asset, AssetHolding, Faucet, FaucetMember,
                                FaucetPayout, FaucetPayoutStatus,
                                LedgerTransaction, to_base_units)
from toto.assets.services import faucets
from toto.assets.services.bootstrap import bootstrap_economy
from toto.assets.testing import TEST_ISSUER_KEY
from toto.core.models import Platform

User = get_user_model()


@override_settings(MONETARY_ISSUER_KEY=TEST_ISSUER_KEY, ASSETS_MONETARY_MASTER=True)
class RoundProbe(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(
            site_name="Test",
            defaults={"author": "t", "publication_year": 2026, "active": True})
        bootstrap_economy()
        self.tpln = Asset.objects.get(unit_name="TPLN")
        self.ada = User.objects.create_user("ada", password="pw")
        self.f = Faucet.objects.create(name="Zloty", asset=self.tpln, active=True)

    def _account(self, user):
        from toto.assets.prepaid import get_or_create_prepaid_account
        a, _ = get_or_create_prepaid_account(user)
        return a

    def test_conversion_table(self):
        for raw in ("0.004", "0.005", "0.006", "0.015", "0.025", "0.035",
                    "1.005", "1.015"):
            nearest = to_base_units(Decimal(raw), 2)
            down = int((Decimal(raw) * 100).to_integral_value(rounding=ROUND_DOWN))
            print(f"  {raw:>6} -> nearest {nearest:>4}   ROUND_DOWN {down:>4}")

    def test_what_a_sub_quantum_rate_actually_pays(self):
        m = FaucetMember.objects.create(faucet=self.f, user=self.ada,
                                        amount_per_hour=Decimal("0.006"))
        report = faucets.run_hour()
        p = FaucetPayout.objects.get()
        h = AssetHolding.objects.get(asset=self.tpln,
                                     account=self._account(self.ada))
        tx = LedgerTransaction.objects.get(reference__startswith="faucet-")
        entries = sorted(e.amount_base_units for e in tx.entries.all())
        print(f"\n  report={report}")
        print(f"  payout.amount_base_units={p.amount_base_units} status={p.status}")
        print(f"  holding={h.balance_base_units}  ledger entries={entries}")
        print(f"  tx.description={tx.description!r}")
        # internal consistency: what the row says == what the ledger moved
        self.assertEqual(p.amount_base_units, h.balance_base_units)
        self.assertEqual(p.amount_base_units, entries[1])
        self.assertEqual(p.status, FaucetPayoutStatus.PAID)

    def test_what_round_down_would_do_instead(self):
        """The finding's implied fix, exercised: rate below half a quantum."""
        FaucetMember.objects.create(faucet=self.f, user=self.ada,
                                    amount_per_hour=Decimal("0.004"))
        report = faucets.run_hour()
        p = FaucetPayout.objects.get()
        print(f"\n  0.004/h under TODAY's nearest-rounding:")
        print(f"  report={report}")
        print(f"  payout.amount_base_units={p.amount_base_units} "
              f"status={p.status} detail={p.detail!r}")
        print(f"  ledger txs={LedgerTransaction.objects.filter(reference__startswith='faucet-').count()}")

    def test_mana_nine_decimals(self):
        mana = Asset.objects.get(unit_name="MANA")
        f = Faucet.objects.create(name="M", asset=mana, active=True)
        FaucetMember.objects.create(faucet=f, user=self.ada,
                                    amount_per_hour=Decimal("0.0000000006"))
        faucets.run_hour()
        p = FaucetPayout.objects.get()
        print(f"\n  MANA(9) 0.0000000006/h -> base units {p.amount_base_units} "
              f"= {p.amount_base_units / 10**9} MANA, status={p.status}")
