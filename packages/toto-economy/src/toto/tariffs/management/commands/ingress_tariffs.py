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
from django.utils import timezone

from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    to_base_units,
)
from toto.assets.queries import get_asset_balance_display
from toto.assets.services.assets import transfer_asset
from toto.mint.services import create_currency
from toto.ingress import IngressCommand
from django.conf import settings

from toto.quota.metrics import registry
from toto.tariffs import rate_card
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


# The three helpers above are still used by the --full demo tariffs, which price
# in assets that are not this host's gas and so cannot go through rate_card.
# Everything on the real rate card does — see process().


def _asset(unit_name, name, decimals=6, total_supply=_DEFAULT_SUPPLY):
    """Find or mint a demo asset. Only used by --full."""
    reserve = _account(f"RES-{unit_name}", f"{name} Reserve", AccountType.RESERVE)
    existing = Asset.objects.filter(unit_name=unit_name).first()

    if existing is None:
        asset = create_currency(
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
    # storage.gb_day is deliberately absent: it is levied nightly by toto.tax
    # for data already sitting on disk, and an automatic recurring charge must
    # be armed by a person at /quota/<metric>/, never by a deploy.
    # Compute — a worker is occupied for real time.
    "texlab.compile": NORMAL,
    "workflows.run": NORMAL,
    # A Jinja pass and one WeasyPrint render on a worker. NORMAL because it is
    # the same engine cyprian.pdf and memo.pdf use; what makes it heavier than
    # those is that it is ASYNCHRONOUS — nobody waits for it — which is why its
    # metric caps at 20 a period rather than their 50. Raise it at the rate desk
    # if a fleet of invoice runs proves costlier than a document export.
    "aralia.render": NORMAL,
    # Lookups — cheap to serve, but they cost someone else's goodwill.
    "assets.chain.verify": CHEAP,
    # The assistant. Two codes because the two things vary independently: how
    # often somebody asks, and how big each ask turns out to be. The token price
    # is the one that matters — a 60-page paste and a one-line question are the
    # same number of requests and are not remotely the same cost.
    # One on-demand file scan. Priced because it occupies a worker on purpose;
    # the automatic door screening stays free — it is the platform protecting
    # itself, not a service somebody ordered.
    "antivirus.scan": CHEAP,
    "ai.request": CHEAP,
    "ai.tokens_1k": NORMAL,
    # A month of a subscription plan. The QUANTITY is the plan's size (Standard
    # is 200 units, Professional 600) less the best community discount, so this
    # is the price of one unit and not of a plan — see subscriptions/services.py
    # for why the variation has to live in the quantity. CHEAP × 200 is a
    # plausible monthly bill; CHEAP × 600 is three times it, as intended.
    "subscription.month": CHEAP,
}


def _mana_pooled() -> tuple[set, set]:
    """``(codes a mana pool prices here, pks of the pool assets)``.

    Both empty without ``toto.mana`` or before its pools exist — then this
    seeder prices every metric in gas, exactly as it always has.
    """
    from django.apps import apps

    if not apps.is_installed("toto.mana"):
        return set(), set()
    from toto.mana.services import pool_asset_ids, pooled_codes

    return pooled_codes(), pool_asset_ids()


def host_prices() -> dict:
    """The rate card this host seeds: the defaults above, under its overrides.

    Hosts run their own economies and the same action is not worth the same on
    each of them — moving a gigabyte through an internal working server is not
    what it is worth to a community. The defaults are a starting point, not a
    platform-wide truth, so a host states its differences in settings::

        TARIFF_PRICES = {"storage.transfer_mb": "0.001"}   # dearer here
        TARIFF_PRICES = {"storage.request": None}          # free here

    A ``None`` removes the price, which is how the free tier is expressed —
    an absence, not a zero. Prices are read as strings and go through Decimal;
    a float would put binary rounding inside a billing path.

    This only seeds. The rate card lives in the database and staff edit it at
    /quota/<metric>/ afterwards; re-running ingress does not undo their edits.

    ``TARIFF_SEED_PRICES = False`` seeds **no** prices at all, which is how a
    host brings the economy up without charging anyone yet: the caps still
    refuse (429), the ledger is live and audited, and the rate desk is real but
    empty. Turning it on is then a deliberate, reviewable act rather than a
    side effect of a deploy. Sizing matters here — a user at their daily caps
    burns the whole GAS_STARTING_GRANT in a few days, and there is no
    self-service top-up.
    """
    from django.conf import settings

    if not getattr(settings, "TARIFF_SEED_PRICES", True):
        return {}

    prices = dict(PRICES)
    for code, value in (getattr(settings, "TARIFF_PRICES", None) or {}).items():
        if value is None:
            prices.pop(code, None)
        else:
            prices[code] = Decimal(str(value))
    return prices


class Command(IngressCommand):
    help = "Seed the platform rate card (prices in ASR) and, with --full, demo tariffs."

    def process(self):
        self.stdout.write("⛽  Seeding tariffs…")

        # Seeding only. What this host BILLS in is its currency contract, not a
        # ticker — see portal/hierarchical_economy.md. This lookup exists to
        # find the asset to hang seed prices on when the contract has not been
        # assigned yet; runtime resolution goes through contractual_asset().
        ticker = getattr(settings, "GAS_ASSET", "ASR")
        gas = Asset.objects.filter(unit_name=ticker).first()
        if gas is None:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ {ticker} not found — run ingress_assets first. No prices seeded."
            ))
            return

        prices = host_prices()
        rate_card.revenue_account()
        self.stdout.write(f"  +/✓ account {rate_card.REVENUE_ACCOUNT_CODE}")

        tariff = rate_card.default_tariff()
        # Deliberately ownerless: this is the platform's rate card, not a
        # person's. `_can_manage` lets any staff member edit an ownerless
        # tariff, which is what makes it reachable from the UI.
        self.stdout.write(f"  +/✓ tariff {rate_card.DEFAULT_TARIFF_CODE}")

        # Mana-drawing metrics are priced by ``ingress_mana`` in their pool's
        # asset. Seeding them here in gas would be overwritten there — or,
        # worse, win on a host where this runs last.
        mana_codes, pool_assets = _mana_pooled()

        priced = 0
        kept = 0
        for metric_spec in registry.all():
            # The mirror rows exist whether or not the metric is priced, so the
            # billing side keeps a complete catalogue of what could be charged.
            rate_card.billing_metric_for(metric_spec)
            if metric_spec.code in mana_codes:
                self.stdout.write(f"  ·   {metric_spec.code} is priced by ingress_mana")
                continue
            price = prices.get(metric_spec.code)
            if price is None:
                # Metered but not priced — free, and deliberately so.
                self.stdout.write(f"  ·   {metric_spec.code} is metered but free")
                continue
            # Seed ONCE. After that the number is staff's: a redeploy that
            # re-wrote every price would undo a rate-desk edit silently (the
            # same rule ingress_mana follows for the pool prices).
            billing_metric = rate_card.billing_metric_for(metric_spec)
            if TariffItem.objects.filter(tariff=tariff, metric=billing_metric).exists():
                kept += 1
                self.stdout.write(f"  ✓   price {metric_spec.code} kept as staff set it")
                continue
            # One implementation, shared with the staff rate desk: if this ever
            # diverges from what a human typing a number gets, the two screens
            # start disagreeing about what anybody pays.
            rate_card.upsert_price(metric_spec, price)
            priced += 1
            self.stdout.write(
                f"  +   price {metric_spec.code} = {price} {ticker}/{metric_spec.unit}"
            )

        # A price whose metric this host does not meter is simply not seeded.
        # This used to assert, which was right while one host installed every
        # metered app: a price nothing meters can never be charged. It stopped
        # being right the moment a second host ran its own rate card — "this
        # host does not install texlab" is not the same fault as "nothing
        # anywhere meters this", and failing the seed for it takes the whole
        # entrypoint down. Report instead, so the omission is visible and the
        # rate card stays a catalogue that each host draws its own subset from.
        not_metered_here = sorted(set(prices) - set(registry.codes()))
        if not_metered_here:
            self.stdout.write(self.style.WARNING(
                f"  ⚠ not priced — this host meters none of: "
                f"{', '.join(not_metered_here)}"
            ))
        assert not (TariffItem.objects.filter(tariff=tariff)
                    .exclude(charged_asset=gas)
                    .exclude(charged_asset__in=pool_assets).exists()), (
            "the default tariff must price everything in gas, except what "
            "draws on a mana pool"
        )

        if not self.full:
            self.stdout.write(self.style.SUCCESS(
                f"✅  Rate card seeded: {priced} priced, {kept} kept, "
                f"of {len(registry)} metrics, in {ticker}."
            ))
            return

        self._seed_demo_tariffs(rate_card.revenue_account(),
                                _bu("request", "Request", "count"))
        self._seed_demo_income(gas, rate_card.revenue_account(),
                               _bu("request", "Request", "count"))
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
            metric = _metric("demo.request", "Demo request",
                             app_label="demo", default_unit=request_unit)
            _item(tariff, metric, "Demo request", banana, Decimal("1"), request_unit, revenue)
            self.stdout.write("  +/✓ demo price demo.request = 1 BANANA/request")

        if makaroni is not None:
            metric = _metric("demo.graph_query", "Graph query",
                             app_label="demo", default_unit=request_unit)
            _item(tariff, metric, "Graph query", makaroni, Decimal("1"), request_unit, revenue)
            self.stdout.write("  +/✓ demo price demo.graph_query = 1 MAKARONI/request")

    # ------------------------------------------------------------------ #
    # Full-only: a little history, so the fees board has something to show #
    # ------------------------------------------------------------------ #

    #: What the demo pretends people did. Deliberately more than one metric —
    #: the point of the income pie is that income has SOURCES, and a pie with
    #: one slice is a circle.
    DEMO_USAGE = [
        ("alice", "storage.request", 4200),
        ("alice", "assets.chain.verify", 90),
        ("bob", "storage.request", 1800),
        ("bob", "storage.transfer_mb", 650),
        ("carol", "storage.transfer_mb", 240),
    ]

    def _seed_demo_income(self, gas, revenue, request_unit):
        """Post real charges from the demo accounts, so the treasury is not empty.

        Real in the sense that matters: these go through `rate_usage_record`
        and `post_usage_record`, so what the fees board reads is genuine posted
        ledger entries and the usage tables agree with the pie. A seeder that
        moved the money directly would show income the charge history could not
        account for.

        The prices live on a **DRAFT** tariff, and that is the load-bearing
        detail. `get_tariff_for_user` falls back to "any ACTIVE tariff with a
        matching item", so an active demo tariff would quietly start charging
        real users for storage — which is precisely what this host's
        TARIFF_SEED_PRICES=False exists to prevent. Draft is never selected;
        rating a record against one explicitly still works.
        """
        from toto.tariffs.models import UsageRecord, UsageStatus
        from toto.tariffs.services import post_usage_record, rate_usage_record

        accounts = {code: LedgerAccount.objects.filter(code=code).first()
                    for code, _, _ in self.DEMO_USAGE}
        if not any(accounts.values()):
            self.stdout.write(self.style.WARNING(
                "  ⚠ no demo accounts — run ingress_assets --full for the history."))
            return

        tariff, _ = _tariff(
            "demo-usage",
            "Demo usage pricing",
            "What the demo accounts were charged, so the fees board has a "
            "history to draw. Draft: it must never price a real user.",
            status=TariffStatus.DRAFT,
        )

        # A metric absent from the registry on this host is simply skipped —
        # the same rule the rate card follows above.
        by_code = {spec.code: spec for spec in registry.all()}
        for code in {code for _, code, _ in self.DEMO_USAGE}:
            spec = by_code.get(code)
            if spec is None:
                continue
            # Priced at exactly what the real rate card would charge, so the
            # demo history is what this platform WOULD have collected rather
            # than a number chosen to look good on a chart.
            _item(tariff, rate_card.billing_metric_for(spec), spec.label or code,
                  gas, PRICES.get(code, CHEAP), rate_card.billing_unit_for(spec),
                  revenue)

        posted = 0
        for account_code, metric_code, quantity in self.DEMO_USAGE:
            account = accounts.get(account_code)
            if account is None or metric_code not in by_code:
                continue
            # Idempotent by construction: one record per (account, metric), so
            # re-running ingress does not keep inflating the treasury.
            record, created = UsageRecord.objects.get_or_create(
                tariff=tariff,
                payer_account=account,
                metric_code=metric_code,
                source_type="ingress.demo",
                source_id=f"{account_code}:{metric_code}",
                defaults={"quantity": Decimal(quantity),
                          "unit": by_code[metric_code].unit or "",
                          "occurred_at": timezone.now()},
            )
            if not created and record.status == UsageStatus.POSTED:
                continue
            _fund(account, gas, Decimal("5"))
            try:
                rate_usage_record(record)
                post_usage_record(record, reference=f"ingress-demo-{record.uuid.hex[:8]}",
                                  description="Ingress seed: demo usage")
            except Exception as exc:  # noqa: BLE001 - demo data is not worth a failed deploy
                self.stdout.write(self.style.WARNING(
                    f"  ⚠ demo charge {metric_code} for {account_code}: {exc}"))
                continue
            posted += 1

        if posted:
            self.stdout.write(self.style.SUCCESS(
                f"  +/✓ {posted} demo charges posted — the fees board has income."))
