"""The one place a price is written.

Two callers set prices: ``manage.py ingress_tariffs``, which seeds the rate card
from ``PRICES``, and the staff rate desk in ``toto.quota``, where somebody types
a number into a grid. They used to be two implementations of the same idea, and
the seeder's was the only one that knew a price needs an asset, a receiving
account and two mirror rows behind it. This module is that knowledge, once.

Everything here may import ``toto.quota`` freely — toto-economy depends on
toto-base, not the other way round. The traffic in the other direction goes
through :mod:`toto.quota.rates`, which never imports this module at module
scope.

**The invariant worth guarding.** ``TariffItem.save()`` derives
``price_per_unit_base_units`` from ``price_per_unit_display``, and only the
derived integer is ever billed. So :func:`upsert_price` sets the display value,
never the integer, always assigns ``charged_asset`` as an object before saving
(``save()`` reads ``.decimals`` off it and silently skips the derivation when it
is unset), and always calls a full ``save()``. Passing ``update_fields`` here
would work until the day someone edits the list, which is how a previous bug let
an edit change every screen and not the number anyone paid.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.urls import NoReverseMatch, reverse

#: The platform's own rate card. Deliberately ownerless — it belongs to the
#: platform rather than a person, which is what lets any staff member edit it
#: (see ``_can_manage`` in views.py).
DEFAULT_TARIFF_CODE = "platform-default"

#: Where the money lands. Mirrors ``get_exchange_fee_account()`` in assets.
REVENUE_ACCOUNT_CODE = "platform-usage-fees"


class NoGasAsset(Exception):
    """This host has no billing asset, so nothing can be priced yet."""


# ---------------------------------------------------------------------------
# The rows a price hangs off
# ---------------------------------------------------------------------------

def gas_asset():
    """The asset metered work is billed in on this platform, or None.

    Resolves through the platform's CURRENCY CONTRACT — the thing that makes
    an asset a currency at all. ``settings.GAS_ASSET`` is not consulted: a
    ticker is a label, and a host does not choose what it bills in. It
    survives only as a genesis-time seed hint in ingress_assets.

    Falls back to the ticker ONLY while no contract exists, so a host mid-way
    through seeding still works; once contracted, the contract wins.
    """
    from toto.assets.contracts import contractual_asset
    from toto.assets.models import Asset

    contracted = contractual_asset()
    if contracted is not None:
        return contracted
    ticker = getattr(settings, "GAS_ASSET", "ASR")
    return Asset.objects.filter(unit_name=ticker, active=True).first()


def revenue_account():
    from toto.assets.models import AccountType, LedgerAccount

    account, _created = LedgerAccount.objects.get_or_create(
        code=REVENUE_ACCOUNT_CODE,
        defaults={"name": "Platform Usage Fees",
                  "account_type": AccountType.SYSTEM, "active": True},
    )
    return account


def default_tariff():
    from .models import Tariff, TariffStatus

    ticker = getattr(settings, "GAS_ASSET", "ASR")
    tariff, created = Tariff.objects.get_or_create(
        code=DEFAULT_TARIFF_CODE,
        defaults={
            "name": "Platform default",
            "description": (f"What metered work costs, in gas ({ticker}). Applies to "
                            "everyone without a more specific tariff."),
            "status": TariffStatus.ACTIVE,
        },
    )
    if not created and tariff.status != TariffStatus.ACTIVE:
        tariff.status = TariffStatus.ACTIVE
        tariff.save(update_fields=["status", "updated_at"])
    return tariff


def billing_unit_for(metric):
    """The BillingUnit mirroring a quota Metric's unit string."""
    from .models import BillingUnit

    unit, _created = BillingUnit.objects.get_or_create(
        code=metric.unit,
        defaults={"label": metric.unit.upper(), "dimension": "",
                  "app_label": "tariffs", "active": True},
    )
    return unit


def billing_metric_for(metric):
    """The BillingMetric mirroring a quota Metric.

    The quota registry is the authority on what exists; this row is the billing
    side's copy of it, so a stale ``app_label`` is repaired rather than trusted.
    """
    from .models import BillingMetric

    unit = billing_unit_for(metric)
    obj, created = BillingMetric.objects.get_or_create(
        code=metric.code,
        defaults={"label": metric.label, "dimension": "",
                  "app_label": metric.app_label, "default_unit": unit,
                  "active": True},
    )
    if not created and obj.app_label != metric.app_label:
        obj.app_label = metric.app_label
        obj.save(update_fields=["app_label"])
    return obj


# ---------------------------------------------------------------------------
# Reading and writing prices
# ---------------------------------------------------------------------------

def resolve_asset(tariff=None, asset=None):
    """Which asset a price is denominated in.

    Order: an explicit choice → the tariff's own default → the host's gas asset.
    Each step is a deliberate widening of who gets to decide, and the last one is
    what keeps every host that has only ever billed in gas working unchanged.

    Raises :class:`NoGasAsset` only when the chain runs out — i.e. nobody picked
    an asset AND the host has none seeded.
    """
    if asset is not None:
        return asset
    if tariff is not None and getattr(tariff, "default_asset_id", None):
        return tariff.default_asset
    resolved = gas_asset()
    if resolved is None:
        raise NoGasAsset(
            f"{getattr(settings, 'GAS_ASSET', 'ASR')} is not seeded — "
            "run `manage.py ingress_assets` before pricing anything."
        )
    return resolved


def upsert_price(metric, display_value, asset=None, tariff=None):
    """Set what one unit of ``metric`` costs.

    ``metric`` is a :class:`toto.quota.metrics.Metric`. Everything else — the
    receiving account, the mirror rows, the tariff — is defaulted, so a caller
    supplies a number and nothing else.

    ``asset`` prices this ONE metric in a currency of its own; omit it and the
    tariff's default (then the host gas asset) decides. That is what lets a rate
    card mix assets per metered thing without every caller having to care.
    """
    from .models import TariffItem

    price = Decimal(str(display_value))
    tariff = tariff or default_tariff()
    resolved_asset = resolve_asset(tariff, asset)
    billing_metric = billing_metric_for(metric)
    unit = billing_unit_for(metric)

    item = TariffItem.objects.filter(tariff=tariff, metric=billing_metric).first()
    if item is None:
        item = TariffItem(tariff=tariff, metric=billing_metric,
                          name=metric.label, unit=unit,
                          receiving_account=revenue_account())
    # An object, not an _id: save() reads .decimals off it to derive the base
    # units, and skips the derivation entirely when it cannot.
    item.charged_asset = resolved_asset
    item.price_per_unit_display = price
    item.active = True
    item.save()
    return item


def remove_price(metric_code: str) -> bool:
    """Make a metric free again. True when a price was actually removed.

    Deletes the TariffItem only. The BillingMetric and BillingUnit mirrors stay:
    ``TariffItem.metric`` and ``UsageCharge.tariff_item`` are both PROTECT, so
    removing them would take the charge history with them.
    """
    from .models import TariffItem

    deleted, _ = TariffItem.objects.filter(
        tariff__code=DEFAULT_TARIFF_CODE, metric__code=metric_code
    ).delete()
    return bool(deleted)


def item_edit_url(metric_code: str) -> str:
    """Where the fields the grid does not express are edited. "" when unpriced."""
    from .models import TariffItem

    item = TariffItem.objects.filter(
        tariff__code=DEFAULT_TARIFF_CODE, metric__code=metric_code
    ).only("pk").first()
    if item is None:
        return ""
    try:
        return reverse("tariffs:tariff_item_edit", args=[item.pk])
    except NoReverseMatch:
        return ""


def rate_card() -> dict[str, dict]:
    """The platform rate card as plain data, keyed by metric code.

    Deliberately no model instances cross this boundary: ``toto.quota`` renders
    these rows, and a template that could reach ``row.item.charged_asset`` would
    blow up on a host with no economy. Strings, Decimals and ints only.
    """
    from .models import TariffItem

    rows: dict[str, dict] = {}
    items = (TariffItem.objects
             .filter(tariff__code=DEFAULT_TARIFF_CODE, active=True)
             .select_related("metric", "unit", "charged_asset"))
    for item in items:
        try:
            url = reverse("tariffs:tariff_item_edit", args=[item.pk])
        except NoReverseMatch:
            url = ""
        rows[item.metric.code] = {
            "price_display": item.price_per_unit_display,
            "asset": item.charged_asset.unit_name,
            # The pk, so a currency picker can pre-select the row's asset. An
            # int, which keeps the "no model instances cross this boundary" rule
            # above intact.
            "asset_id": item.charged_asset_id,
            "asset_decimals": item.charged_asset.decimals,
            "unit_code": item.unit.code if item.unit_id else "",
            "unit_quantity": item.unit_quantity,
            "minimum_charge_base_units": item.minimum_charge_base_units,
            "active": item.active,
            "item_pk": item.pk,
            "advanced_url": url,
        }
    return rows


def parse_price(raw) -> Decimal | None:
    """A staff-typed price, or None when the field was left blank.

    Raises ValueError on anything that is neither. Lives here so the quota side
    never needs a tariffs form class to validate a number.
    """
    text = (raw or "").strip() if isinstance(raw, str) else raw
    if text in ("", None):
        return None
    try:
        value = Decimal(str(text))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{raw!r} is not a number.")
    if value < 0:
        raise ValueError("A price cannot be negative.")
    return value


def price_asset_symbol() -> str:
    """The ticker prices are quoted in, for a column header. "" when unseeded."""
    asset = gas_asset()
    return asset.unit_name if asset else ""
