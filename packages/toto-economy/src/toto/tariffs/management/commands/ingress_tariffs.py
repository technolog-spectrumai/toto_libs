"""Seed the platform rate card.

Everything is priced in this host's gas asset (settings.GAS_ASSET, ASR by
default) because that is the only spendable asset a
real build has. Prices are per operation and deliberately small: the point is
to make abuse expensive, not to make ordinary use a budgeting exercise.

Sizing them is arithmetic against a fixed supply. ASR is minted once at
6,666.666666667 (9 decimals) and cannot be minted again, so with a 0.1 ASR
starting grant the reserve funds ~66,000 accounts, and at 0.00001 ASR per
operation each account gets ~10,000 actions before it needs a top-up. Move one
number and the other has to move with it — see gas.md.

    manage.py ingress_tariffs           the ASR rate card
    manage.py ingress_tariffs --full    plus demo prices in BANANA and MAKARONI

Revenue lands in `platform-usage-fees`, a system account created on first use,
mirroring `get_exchange_fee_account()` in assets.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from django.db.models import Sum

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    to_base_units,
)
from toto.assets.queries import get_asset_balance_display
from toto.assets.services.assets import create_asset, transfer_asset
from toto.ingress import IngressCommand
from django.conf import settings

from toto.quota.metrics import registry
from toto.tariffs.models import (
    BillingMetric,
    BillingUnit,
    RoundingMode,
    Tariff,
    TariffItem,
    TariffStatus,
)

# Default total supply for a demo-only asset: 1 billion display units.
_DEFAULT_SUPPLY = Decimal("1000000000")

# The unit price for one metered operation, in ASR. One order of magnitude
# apart so a heavy job costs visibly more than a cheap lookup.
CHEAP = Decimal("0.00001")     # a lookup: geocoding, a chain verify
NORMAL = Decimal("0.0001")     # a real job: a compile, a workflow run
EXPENSIVE = Decimal("0.001")   # minutes of CPU: ffmpeg, transcription


def _bu(code, label, dimension=""):
    obj, _ = BillingUnit.objects.get_or_create(
        code=code,
        defaults={"label": label, "dimension": dimension, "app_label": "tariffs", "active": True},
    )
    return obj


def _metric(code, label, dimension="", app_label="tariffs", default_unit=None):
    obj, created = BillingMetric.objects.get_or_create(
        code=code,
        defaults={
            "label": label,
            "dimension": dimension,
            "app_label": app_label,
            "default_unit": default_unit,
            "active": True,
        },
    )
    if not created and obj.app_label != app_label:
        obj.app_label = app_label
        obj.save(update_fields=["app_label"])
    return obj


def _account(code, name, account_type=AccountType.SYSTEM):
    acc, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={"name": name, "account_type": account_type, "active": True},
    )
    return acc


def _asset(unit_name, name, decimals=6, total_supply=_DEFAULT_SUPPLY):
    """Find or mint a demo asset. Only used by --full."""
    reserve = _account(f"RES-{unit_name}", f"{name} Reserve", AccountType.RESERVE)
    existing = Asset.objects.filter(unit_name=unit_name).first()

    if existing is None:
        asset = create_asset(
            name=name,
            unit_name=unit_name,
            total_supply=total_supply,
            decimals=decimals,
            reserve_account=reserve,
            reference=f"ingress-create-{unit_name.lower()}",
            description=f"Ingress: initial issuance of {name}",
        )
        asset.reserve_account = reserve
        asset.save(update_fields=["reserve_account", "updated_at"])
        return asset

    asset = existing
    if not asset.reserve_account_id:
        asset.reserve_account = reserve
        asset.save(update_fields=["reserve_account", "updated_at"])
    return asset


def _fund(account, asset, display_amount):
    """Top an account up to at least display_amount. Idempotent."""
    if not asset.reserve_account_id:
        return
    to_add = Decimal(str(display_amount)) - get_asset_balance_display(asset, account)
    if to_add <= 0:
        return
    try:
        transfer_asset(
            asset=asset,
            sender_account=asset.reserve_account,
            receiver_account=account,
            amount=to_add,
            reference=f"ingress-fund-{account.code}-{asset.unit_name.lower()}-{uuid.uuid4().hex[:6]}",
            description=f"Ingress seed: fund {account.code}",
        )
    except Exception:
        pass


def _tariff(code, name, description, status=TariffStatus.ACTIVE):
    t, created = Tariff.objects.get_or_create(
        code=code,
        defaults={"name": name, "description": description, "status": status},
    )
    if not created and t.status != status:
        t.status = status
        t.save(update_fields=["status", "updated_at"])
    return t, created


def _item(tariff, metric, name, asset, price, unit, recv, uq=1,
          rounding=RoundingMode.UP, min_charge=0):
    price_dec = Decimal(str(price))
    price_base = to_base_units(price_dec, asset.decimals)
    item, created = TariffItem.objects.get_or_create(
        tariff=tariff,
        metric=metric,
        defaults={
            "name": name,
            "charged_asset": asset,
            "price_per_unit_display": price_dec,
            "price_per_unit_base_units": price_base,
            "unit": unit,
            "unit_quantity": Decimal(str(uq)),
            "receiving_account": recv,
            "rounding_mode": rounding,
            "minimum_charge_base_units": min_charge,
            "active": True,
        },
    )
    if not created and item.price_per_unit_base_units != price_base:
        item.price_per_unit_display = price_dec
        item.price_per_unit_base_units = price_base
        item.save(update_fields=["price_per_unit_display", "price_per_unit_base_units", "updated_at"])
    return item


# What each metric costs. The metric's code, label, unit and owning app come
# from the registry an app declares in its own metrics.py — this file only says
# what to charge, which is the one thing the library must not know.
#
# A registered metric absent from this table is FREE. That is how the free tier
# is expressed, and it is why the five metrics that were priced here but never
# charged by any code are gone: pricing something nothing meters was fiction.
PRICES = {
    # Storage — charged by size as well as per call.
    "storage.request": CHEAP,
    "storage.transfer_mb": CHEAP,
    # Compute — a worker is occupied for real time.
    "texlab.compile": NORMAL,
    "workflows.run": NORMAL,
    # Lookups — cheap to serve, but they cost someone else's goodwill.
    "assets.chain.verify": CHEAP,
}


class Command(IngressCommand):
    help = "Seed the platform rate card (prices in ASR) and, with --full, demo tariffs."

    def process(self):
        self.stdout.write("⛽  Seeding tariffs…")

        # The gas ticker is per host: two hosts running their own ledgers must
        # not both bill in "ASR", because the balances are not interchangeable.
        ticker = getattr(settings, "GAS_ASSET", "ASR")
        gas = Asset.objects.filter(unit_name=ticker).first()
        if gas is None:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ {ticker} not found — run ingress_assets first. No prices seeded."
            ))
            return

        revenue = _account("platform-usage-fees", "Platform Usage Fees")
        self.stdout.write("  +/✓ account platform-usage-fees")

        tariff, _created = _tariff(
            "platform-default",
            "Platform default",
            f"What metered work costs, in gas ({ticker}). Applies to everyone "
            "without a more specific tariff.",
        )
        # Deliberately ownerless: this is the platform's rate card, not a
        # person's. `_can_manage` lets any staff member edit an ownerless
        # tariff, which is what makes it reachable from the UI.
        self.stdout.write("  +/✓ tariff platform-default")

        units: dict[str, BillingUnit] = {}
        priced = 0
        for metric_spec in registry.all():
            unit = units.get(metric_spec.unit)
            if unit is None:
                unit = units[metric_spec.unit] = _bu(
                    metric_spec.unit, metric_spec.unit.upper(), ""
                )
            metric = _metric(
                metric_spec.code,
                metric_spec.label,
                app_label=metric_spec.app_label,
                default_unit=unit,
            )
            price = PRICES.get(metric_spec.code)
            if price is None:
                # Metered but not priced — free, and deliberately so.
                self.stdout.write(f"  ·   {metric_spec.code} is metered but free")
                continue
            _item(tariff, metric, metric_spec.label, gas, price, unit, revenue)
            priced += 1
            self.stdout.write(f"  +/✓ price {metric_spec.code} = {price} {ticker}/{unit.code}")

        # A price whose metric this host does not meter is simply not seeded.
        # This used to assert, which was right while one host installed every
        # metered app: a price nothing meters can never be charged. It stopped
        # being right the moment a second host ran its own rate card — "this
        # host does not install texlab" is not the same fault as "nothing
        # anywhere meters this", and failing the seed for it takes the whole
        # entrypoint down. Report instead, so the omission is visible and the
        # rate card stays a catalogue that each host draws its own subset from.
        not_metered_here = sorted(set(PRICES) - set(registry.codes()))
        if not_metered_here:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ not priced — this host meters none of: "
                f"{', '.join(not_metered_here)}"
            ))
        assert not TariffItem.objects.filter(tariff=tariff).exclude(charged_asset=gas).exists(), (
            "the default tariff must price everything in gas"
        )

        if not self.full:
            self.stdout.write(self.style.SUCCESS(
                f"✅  Rate card seeded: {priced} of {len(registry)} metrics priced in {ticker}."
            ))
            return

        self._seed_demo_tariffs(revenue, _bu("request", "Request", "count"))
        self.stdout.write(self.style.SUCCESS("✅  Tariffs ingress complete."))

    # ------------------------------------------------------------------ #
    # Full-only: prices denominated in the demo tokens                    #
    # ------------------------------------------------------------------ #

    def _seed_demo_tariffs(self, revenue, request_unit):
        """Show that a price row can name any asset, not just the gas.

        BANANA and MAKARONI only exist under `ingress_assets --full`, so this
        tariff is demonstration material and is left in draft — it must never
        outrank the default rate card by accident.
        """
        banana = Asset.objects.filter(unit_name="BANANA").first()
        makaroni = Asset.objects.filter(unit_name="MAKARONI").first()
        if banana is None and makaroni is None:
            self.stdout.write(self.style.WARNING(
                "  ⚠ no demo tokens — run ingress_assets --full for BANANA/MAKARONI."
            ))
            return

        tariff, _ = _tariff(
            "demo-tokens",
            "Demo token pricing",
            "Prices denominated in the playful per-resource tokens. Draft: it "
            "demonstrates multi-asset pricing and is not applied to anyone.",
            status=TariffStatus.DRAFT,
        )

        if banana is not None:
            metric = _metric("demo.ai_inference", "AI inference",
                             app_label="demo", default_unit=request_unit)
            _item(tariff, metric, "AI inference", banana, Decimal("1"), request_unit, revenue)
            self.stdout.write("  +/✓ demo price demo.ai_inference = 1 BANANA/request")

        if makaroni is not None:
            metric = _metric("demo.graph_query", "Graph query",
                             app_label="demo", default_unit=request_unit)
            _item(tariff, metric, "Graph query", makaroni, Decimal("1"), request_unit, revenue)
            self.stdout.write("  +/✓ demo price demo.graph_query = 1 MAKARONI/request")
