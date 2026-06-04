"""
simulate_treasury — seed realistic ledger flows for all communities so the
Treasury dashboard has data to display during user testing.

Run:
  python manage.py simulate_treasury
  python manage.py simulate_treasury --months 6
  python manage.py simulate_treasury --reset   (wipe sim entries first)

Creates one LedgerTransaction per (community, source_type, month) for each
inflow and outflow category, spreading amounts realistically over time.
All generated transactions carry source_id="sim" so they are easy to identify
and delete with --reset.
"""
from __future__ import annotations

import random
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from toto.assets.models import (
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    TransactionType,
    to_base_units,
)
from toto.ingress import IngressCommand
from toto.socialhub.treasury import get_or_create_treasury_account

SIM_MARKER = "sim"   # stored in LedgerTransaction.source_id to identify seeded rows

# ---------------------------------------------------------------------------
# Flow templates  (source_type, label, monthly_base, is_inflow)
# ---------------------------------------------------------------------------
INFLOWS = [
    ("tariff.revenue",       "Tariff Revenue",       8_000),
    ("subscription.revenue", "Subscription Revenue", 5_500),
    ("poll_tax",             "Community Poll Tax",    3_200),
    ("bourse.fee",           "Trading Fees",         1_800),
    ("community.deposit",    "Member Contributions", 2_000),
]

OUTFLOWS = [
    ("payroll.expense",      "Payroll",              -6_500),
    ("operations.expense",   "Operations",           -3_000),
    ("infrastructure.cost",  "Infrastructure",       -2_200),
    ("grants.expense",       "Community Grants",     -1_000),
]

ALL_FLOWS = INFLOWS + OUTFLOWS


def _jitter(amount: int) -> int:
    """±20 % random noise so the chart looks organic."""
    return int(amount * random.uniform(0.80, 1.20))


class Command(IngressCommand):
    help = "Seed simulated treasury cash flows for user-testing demos."

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument("--months",  type=int, default=12, help="Number of past months to simulate (default 12).")
        parser.add_argument("--reset",   action="store_true",  help="Delete previously generated simulation entries first.")

    def process(self):
        months = self.options.get("months", 12)
        reset  = self.options.get("reset", False)

        if reset:
            self._reset_sim_data()

        asset = self._pick_asset()
        if asset is None:
            self.stdout.write(self.style.ERROR("  No active asset found. Run ingress_assets first."))
            return

        self.stdout.write(f"  Using asset: {asset.unit_name}")

        from toto.socialhub.models import Community
        communities = list(Community.objects.all())
        if not communities:
            self.stdout.write(self.style.WARNING("  No communities found. Run ingress_socialhub first."))
            return

        for community in communities:
            self._seed_community(community, asset, months)

        self.stdout.write(self.style.SUCCESS(
            f"  Simulation complete — {len(communities)} communities × {months} months."
        ))

    # ------------------------------------------------------------------

    def _reset_sim_data(self):
        deleted, _ = LedgerTransaction.objects.filter(source_id=SIM_MARKER).delete()
        self.stdout.write(self.style.WARNING(f"  Deleted {deleted} simulation transactions."))

    def _pick_asset(self) -> "Asset | None":
        for unit in ("COMMUNITY_TOKEN", "PLN", "USD", "EUR", "STORAGE_TOKEN"):
            a = Asset.objects.filter(unit_name=unit, active=True).first()
            if a:
                return a
        return Asset.objects.filter(active=True).first()

    def _seed_community(self, community, asset: Asset, months: int):
        treasury_account, _ = get_or_create_treasury_account(community)

        # Ensure a holding row exists so the community appears in the list view.
        holding, _ = AssetHolding.objects.get_or_create(
            asset=asset,
            account=treasury_account,
            defaults={"balance_base_units": 0},
        )

        now = timezone.now()
        created_count = 0

        for month_offset in range(months, 0, -1):
            month_dt = (now.replace(day=1) - timedelta(days=30 * month_offset)).replace(
                hour=12, minute=0, second=0, microsecond=0
            )

            for src_type, description, monthly_amount in ALL_FLOWS:
                ref = f"sim:{community.pk}:{src_type}:{month_dt.strftime('%Y-%m')}"
                if LedgerTransaction.objects.filter(reference=ref).exists():
                    continue

                amount_base = to_base_units(
                    Decimal(str(abs(_jitter(monthly_amount)))), asset.decimals
                )
                is_inflow = monthly_amount > 0

                with transaction.atomic():
                    tx = LedgerTransaction.objects.create(
                        reference=ref,
                        transaction_type=TransactionType.ASSET_TRANSFER,
                        description=description,
                        source_type=src_type,
                        source_id=SIM_MARKER,
                        asset=asset,
                        posted=False,
                    )

                    entry_amount = amount_base if is_inflow else -amount_base
                    LedgerEntry.objects.create(
                        transaction=tx,
                        account=treasury_account,
                        asset=asset,
                        amount_base_units=entry_amount,
                    )

                    # Update holding balance to reflect the flow.
                    AssetHolding.objects.filter(pk=holding.pk).update(
                        balance_base_units=holding.balance_base_units + entry_amount
                    )
                    holding.refresh_from_db()

                    tx.posted = True
                    tx.save(update_fields=["posted"])

                    # Backdate to the correct month so history charts work.
                    LedgerTransaction.objects.filter(pk=tx.pk).update(created_at=month_dt)

                created_count += 1

        self.stdout.write(f"  {community.name}: {created_count} new entries seeded.")
