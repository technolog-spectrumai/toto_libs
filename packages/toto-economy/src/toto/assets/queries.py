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


def get_asset_max_supply(asset: Asset) -> int:
    """The ceiling engraved into this asset's identity, in base units."""
    return asset.max_supply_base_units


def get_asset_issued_supply(asset: Asset) -> int:
    """How much of this asset the LEDGER says exists here, in base units.

    Units enter the world at ``system_issuance`` and leave it there, so the
    negative of that account's position is exactly what has been issued and
    not destroyed — computed from postings alone, with no reference to any
    column and no need for the mint to be installed.

    This is deliberately a SECOND opinion. ``toto.mint.history.supply()`` is
    the chain's answer, this is the ledger's, and the two disagreeing is a
    finding rather than an inconvenience. On a branch both are zero: a branch
    issues nothing, it receives allocations from an external counterparty.
    """
    system = LedgerAccount.objects.filter(code="system_issuance").first()
    if system is None:
        return 0
    posted = LedgerEntry.objects.filter(asset=asset, account=system).aggregate(
        total=Sum("amount_base_units"))["total"] or 0
    return -posted


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
    issued = get_asset_issued_supply(asset)
    diff = holdings_sum - issued
    return {
        # Do the balances people hold add up to what was actually issued?
        # Against the MAXIMUM this would be meaningless: a currency may sit
        # engraved and part-minted for as long as its issuer likes.
        "supply_matches": holdings_sum == issued,
        "issued_supply": issued,
        "issued_supply_display": from_base_units(issued, asset.decimals),
        "entries_balanced": entries_sum == 0,
        "holdings_sum": holdings_sum,
        "holdings_sum_display": from_base_units(holdings_sum, asset.decimals),
        "entries_sum": entries_sum,
        "supply_diff": diff,
        "supply_diff_display": from_base_units(abs(diff), asset.decimals),
        "supply_diff_sign": "+" if diff > 0 else ("-" if diff < 0 else ""),
    }
