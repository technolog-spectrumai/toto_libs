"""Issuing an asset: the ledger movement plus the record of who and why."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction


def issue_asset(*, name: str, unit_name: str, total_supply: Decimal,
                decimals: int, reason: str, actor=None, code: str = "",
                symbol: str = "", reserve_code: str = "currency-reserve"):
    """Create a new fixed-supply asset and record the decision.

    The whole supply is created once, into the asset's reserve. There is no
    second act: releasing it later is an ordinary transfer, and there is no
    verb anywhere that makes more.
    """
    from toto.assets.models import (AccountType, LedgerAccount,
                                    LedgerTransaction, TransactionType)
    from toto.assets.services.assets import create_asset

    from .models import IssuanceRecord

    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(
            "Issuing an asset needs a reason — it is the only record of why "
            "this exists, and it cannot be added afterwards.")

    with transaction.atomic():
        reserve, _ = LedgerAccount.objects.get_or_create(
            code=reserve_code,
            defaults={"name": "Currency Reserve",
                      "account_type": AccountType.RESERVE, "active": True})

        asset = create_asset(
            name=name, unit_name=unit_name, total_supply=total_supply,
            decimals=decimals, reserve_account=reserve,
            reference=f"issue-{unit_name.lower()}-{IssuanceRecord.objects.count() + 1}",
            description=reason, code=code, symbol=symbol)
        asset.reserve_account = reserve
        asset.save(update_fields=["reserve_account", "updated_at"])

        creation = LedgerTransaction.objects.filter(
            asset=asset,
            transaction_type=TransactionType.ASSET_CREATE).first()

        IssuanceRecord.objects.create(
            asset=asset, currency_hash=asset.currency_hash,
            unit_name=asset.unit_name,
            total_supply_base_units=asset.total_supply_base_units,
            decimals=asset.decimals, actor=actor, reason=reason,
            genesis_payload=asset.genesis_payload,
            genesis_signature=asset.genesis_signature,
            ledger_transaction=creation)
        return asset
