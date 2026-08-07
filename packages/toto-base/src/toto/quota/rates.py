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
    return _rate_card()


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
