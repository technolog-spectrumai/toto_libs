"""Reading and writing prices from the limits side of metering.

``toto.quota`` ships in a wheel to every host; the rate card it would price
against, ``toto.tariffs``, ships in ``toto-economy``, which aurelian and studio
do not pin at all. So ``toto.tariffs`` there is not merely uninstalled — it is
not importable, and nothing in this package may name it outside a function body.

:mod:`toto.quota.charge` is the same trick for *spending* money. This module is
the same trick for *quoting* it, kept separate because the audiences differ:
charge.py is on the critical path of every metered request, while this is read
by two staff-facing screens. Growing charge.py with a bulk editor's needs would
put that blast radius on every upload.

Every function degrades to an empty answer rather than raising, so a caller
never needs a guard::

    prices = rates.rate_card()          # {} on a host with no economy
    rates.set_price("storage.request", "0.00002")   # False, changed nothing

**Only plain data crosses the boundary.** No model instance is ever returned —
a quota template that could reach ``row.item.charged_asset.unit_name`` would
blow up on exactly the hosts this indirection exists to protect.
"""

from __future__ import annotations

from django.db import DatabaseError


def pricing_enabled() -> bool:
    """True when this host has a rate card to read or write."""
    from .charge import billing_enabled

    return billing_enabled()


def rate_card() -> dict[str, dict]:
    """The platform rate card as plain data, keyed by metric code. {} when unbilled."""
    if not pricing_enabled():
        return {}
    try:
        from toto.tariffs.rate_card import rate_card as _rate_card
    except ImportError:  # pragma: no cover - app installed, wheel absent
        return {}
    try:
        return _rate_card()
    except DatabaseError:
        # The app is installed and its tables are not there yet: a database
        # mid-migrate, a scratch shell, a fresh deploy between `migrate` and the
        # tariffs seeder. This module's contract is an empty answer rather than
        # an exception, and until the price hint arrived the only thing that
        # would have noticed was a staff screen. Now every toolbar with a
        # {% price_hint %} on it asks this question on every render, so a raise
        # here is a 500 on half the product instead of one page.
        return {}


def price_of(metric_code: str) -> dict | None:
    """One metric's price row, or None when it is free or nothing bills."""
    return rate_card().get(metric_code)


def billing_assets() -> list[dict]:
    """The assets a price may be denominated in: ``[{id, symbol, name}]``.

    Empty when nothing bills or the assets app is absent, which is exactly when
    the rate desk should not render a currency column at all.
    """
    if not pricing_enabled():
        return []
    try:
        from toto.assets.models import Asset
    except ImportError:  # pragma: no cover - app installed, wheel absent
        return []
    return [
        {"id": a.pk, "symbol": a.unit_name, "name": a.name}
        for a in Asset.objects.filter(active=True).order_by("unit_name")
    ]


def set_charging_currency(asset_id) -> str | None:
    """Re-denominate every price in one asset. Returns its symbol, or None.

    ONE charging currency for the whole platform. There is no exchange rate
    anywhere in this ledger — ``get_exchange_rate`` refuses every cross-asset
    pair — so a rate card that mixes currencies cannot be added up, compared or
    reasoned about; ``ingress_tariffs`` already asserted everything must be
    priced in one asset, and this is how an operator satisfies that in one act
    instead of row by row.

    Only the DENOMINATION moves. The numbers are left exactly as they are: this
    is not a conversion, and pretending otherwise would invent a rate the ledger
    refuses to have.
    """
    if not pricing_enabled():
        return None
    try:
        from toto.assets.models import Asset
        from toto.tariffs.models import TariffItem
    except ImportError:  # pragma: no cover - app installed, wheel absent
        return None

    asset = Asset.objects.filter(pk=asset_id, active=True).first()
    if asset is None:
        raise ValueError("That currency is not available on this host.")

    # 1. Remember it. `resolve_asset` reads tariff.default_asset before falling
    #    back to the contract's gas asset, so this is the one persistent place a
    #    platform can say "charge in X" — and without it the NEXT price written
    #    would silently revert to the contract asset. (Nothing set this field
    #    before, which is why it looked like a field with no purpose.)
    from toto.tariffs.rate_card import default_tariff

    tariff = default_tariff()
    if tariff is not None and tariff.default_asset_id != asset.pk:
        tariff.default_asset = asset
        tariff.save(update_fields=["default_asset"])

    # 2. Re-denominate what already exists, one save() each rather than a bulk
    #    update: `TariffItem.save` derives price_per_unit_base_units from the
    #    DISPLAY price and the asset's decimals, so a bulk update would leave
    #    every existing price billing the wrong integer the moment the new asset
    #    has a different `decimals`. The displayed number is deliberately kept —
    #    this changes the denomination, not the price, because there is no
    #    exchange rate anywhere in this ledger to convert with.
    #
    #    Items priced in a mana pool's asset are skipped: that asset is what a
    #    member's pool holds, and moving one into the charging currency would
    #    bill a wallet no member can see.
    for item in (TariffItem.objects.exclude(charged_asset=asset)
                 .exclude(charged_asset_id__in=_pool_asset_ids())
                 .select_related("charged_asset")):
        item.charged_asset = asset
        item.save(update_fields=["charged_asset", "price_per_unit_base_units"])
    return asset.unit_name


def _pool_asset_ids() -> set:
    """Assets a mana pool is bound to. Lazy, and empty where mana is absent —
    toto-base may not import toto-economy at module scope."""
    from django.apps import apps

    if not apps.is_installed("toto.mana"):
        return set()
    try:
        from toto.mana.services import pool_asset_ids
    except ImportError:  # pragma: no cover
        return set()
    return pool_asset_ids()


def set_price(metric_code: str, raw, asset_id=None) -> bool:
    """Price a metric from a raw form value. False when nothing was written.

    Takes the string straight off the POST and parses it here, so the limits
    side never needs a tariffs form class. Returns False rather than raising for
    an unbilled host or an unregistered code; a *malformed* number is the
    caller's mistake and raises ValueError.

    ``asset_id`` prices this one metric in its own currency. None means "inherit"
    — the tariff's default, then the host gas asset — so a caller that does not
    offer the choice behaves exactly as it did before the column existed. An id
    that matches no active asset also falls back rather than failing: a stale
    <option> should not cost somebody their price edit.
    """
    if not pricing_enabled():
        return False

    from .metrics import registry

    metric = registry.get(metric_code)
    if metric is None:
        return False

    try:
        from toto.tariffs.rate_card import NoGasAsset, parse_price, upsert_price
    except ImportError:  # pragma: no cover
        return False

    value = parse_price(raw)
    if value is None:
        return clear_price(metric_code)
    try:
        upsert_price(metric, value, asset=_asset_by_id(asset_id))
    except NoGasAsset:
        return False
    return True


def _asset_by_id(asset_id):
    """An active Asset for ``asset_id``, or None to inherit. Never raises."""
    if not asset_id:
        return None
    try:
        from toto.assets.models import Asset
    except ImportError:  # pragma: no cover
        return None
    return Asset.objects.filter(pk=asset_id, active=True).first()


def clear_price(metric_code: str) -> bool:
    """Make a metric free again by removing its price row.

    Free is an absence rather than a zero, so this deletes rather than writing 0
    — the same rule ``ingress_tariffs`` follows for a ``None`` in TARIFF_PRICES.
    """
    if not pricing_enabled():
        return False
    try:
        from toto.tariffs.rate_card import remove_price
    except ImportError:  # pragma: no cover
        return False
    return remove_price(metric_code)


def advanced_url(metric_code: str) -> str:
    """Where the fields a grid cannot express are edited. "" when there are none.

    Returned as a string rather than reversed in a template: a quota template
    naming ``tariffs:`` would be one forgotten guard away from a 500 on a host
    that ships no tariffs.
    """
    if not pricing_enabled():
        return ""
    try:
        from toto.tariffs.rate_card import item_edit_url
    except ImportError:  # pragma: no cover
        return ""
    return item_edit_url(metric_code)


def price_asset_symbol() -> str:
    """The ticker prices are quoted in, for a column header. "" when unbilled."""
    if not pricing_enabled():
        return ""
    try:
        from toto.tariffs.rate_card import price_asset_symbol as _symbol
    except ImportError:  # pragma: no cover
        return ""
    return _symbol()


def spend_by_metric(user, since=None) -> dict[str, dict]:
    """What this user has actually paid, per metric. {} when unbilled.

    Sums the ledger side rather than multiplying usage by the current price:
    a price can change, and what someone was charged last week is history, not
    arithmetic.
    """
    if not pricing_enabled() or user is None or not user.is_authenticated:
        return {}
    try:
        from django.db.models import Sum

        from toto.assets.models import from_base_units
        from toto.tariffs.models import UsageCharge
    except ImportError:  # pragma: no cover
        return {}

    charges = UsageCharge.objects.filter(usage_record__payer_account__user=user)
    if since is not None:
        charges = charges.filter(usage_record__occurred_at__gte=since)

    rows: dict[str, dict] = {}
    grouped = (charges
               .values("usage_record__metric_code",
                       "charged_asset__unit_name", "charged_asset__decimals")
               .annotate(total=Sum("amount_base_units")))
    for row in grouped:
        rows[row["usage_record__metric_code"]] = {
            "amount": from_base_units(row["total"] or 0,
                                      row["charged_asset__decimals"] or 0),
            "asset": row["charged_asset__unit_name"],
        }
    return rows


def balance_of(user) -> dict | None:
    """The user's gas balance, or None when nothing bills."""
    if not pricing_enabled() or user is None or not user.is_authenticated:
        return None
    try:
        from toto.assets.models import AssetHolding, from_base_units
        from toto.tariffs.charge import _get_billing_account
        from toto.tariffs.rate_card import gas_asset
    except ImportError:  # pragma: no cover
        return None

    asset = gas_asset()
    if asset is None:
        return None
    try:
        account = _get_billing_account(user)
    except Exception:  # pragma: no cover - no prepaid account yet
        return None
    if account is None:
        return None

    holding = AssetHolding.objects.filter(account=account, asset=asset).first()
    base_units = holding.balance_base_units if holding else 0
    return {
        "amount": from_base_units(base_units, asset.decimals),
        "asset": asset.unit_name,
    }


def wallet_url() -> str:
    """Where a user tops up — or would, if a top-up existed. "" when unbilled."""
    if not pricing_enabled():
        return ""
    from django.urls import NoReverseMatch, reverse

    try:
        return reverse("assets:wallet")
    except NoReverseMatch:
        return ""
