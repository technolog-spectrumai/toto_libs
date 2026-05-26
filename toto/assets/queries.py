from decimal import Decimal

from django.db import models as db_models
from django.db.models import QuerySet, Sum

from .models import Asset, AssetHolding, LedgerAccount, LedgerEntry, LedgerTransaction, from_base_units


def get_asset_balance(asset: Asset, account: LedgerAccount) -> int:
    try:
        return AssetHolding.objects.get(asset=asset, account=account).balance_base_units
    except AssetHolding.DoesNotExist:
        return 0


def get_asset_balance_display(asset: Asset, account: LedgerAccount) -> Decimal:
    return from_base_units(get_asset_balance(asset, account), asset.decimals)


def get_or_create_holding(asset: Asset, account: LedgerAccount) -> AssetHolding:
    holding, _ = AssetHolding.objects.get_or_create(
        asset=asset,
        account=account,
        defaults={"balance_base_units": 0},
    )
    return holding


def list_asset_holders(asset: Asset) -> QuerySet:
    return (
        AssetHolding.objects.filter(asset=asset, balance_base_units__gt=0)
        .select_related("account")
        .order_by("-balance_base_units")
    )


def get_asset_total_supply(asset: Asset) -> int:
    return asset.total_supply_base_units


def get_transaction_by_reference(reference: str) -> LedgerTransaction:
    return LedgerTransaction.objects.get(reference=reference)


def get_account_asset_movements(account: LedgerAccount, asset: Asset | None = None) -> QuerySet:
    qs = LedgerEntry.objects.filter(account=account).select_related("transaction", "asset", "account")
    if asset is not None:
        qs = qs.filter(asset=asset)
    return qs.order_by("created_at")


def verify_asset_ledger(asset: Asset) -> dict:
    holdings_sum = (
        AssetHolding.objects.filter(asset=asset).aggregate(total=Sum("balance_base_units"))["total"]
        or 0
    )
    entries_sum = (
        LedgerEntry.objects.filter(asset=asset).aggregate(total=Sum("amount_base_units"))["total"]
        or 0
    )
    diff = holdings_sum - asset.total_supply_base_units
    return {
        "total_supply_matches": holdings_sum == asset.total_supply_base_units,
        "entries_balanced": entries_sum == 0,
        "holdings_sum": holdings_sum,
        "holdings_sum_display": from_base_units(holdings_sum, asset.decimals),
        "entries_sum": entries_sum,
        "supply_diff": diff,
        "supply_diff_display": from_base_units(abs(diff), asset.decimals),
        "supply_diff_sign": "+" if diff > 0 else ("-" if diff < 0 else ""),
    }
