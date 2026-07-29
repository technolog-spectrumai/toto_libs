"""
Tariff billing services.

Flow:
  1. calculate_tariff_charge — pure math, no DB writes
  2. check_can_afford        — read-only balance check for a batch of charges
  3. rate_usage_record       — creates UsageCharge rows, marks record as RATED
  4. post_usage_record       — drains prepaid holdings, writes ledger, marks POSTED
  5. record_and_post_usage   — convenience wrapper: create record then post
  6. simulate_tariff         — like rate but returns dict, no DB writes
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from toto.assets.models import (
    AssetHolding,
    LedgerEntry,
    LedgerTransaction,
    TransactionType,
    from_base_units,
    to_base_units,
)

from .models import (
    RoundingMode,
    Tariff,
    TariffItem,
    UsageCharge,
    UsageRecord,
    UsageStatus,
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

@dataclass
class ChargeDraft:
    tariff_item: TariffItem
    quantity: Decimal
    unit: str
    amount_base_units: int
    payer_account_id: int
    receiving_account_id: int
    asset_id: int
    metadata: dict = field(default_factory=dict)


def _apply_rounding(value: Decimal, mode: str) -> int:
    if mode == RoundingMode.UP:
        return int(value.to_integral_value(rounding=ROUND_CEILING))
    if mode == RoundingMode.DOWN:
        return int(value.to_integral_value(rounding=ROUND_FLOOR))
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def _calculate_charge(item: TariffItem, quantity: Decimal) -> int:
    """Return charge in base units for the given item and quantity."""
    raw = Decimal(str(quantity)) * Decimal(item.price_per_unit_base_units) / Decimal(str(item.unit_quantity))
    charged = _apply_rounding(raw, item.rounding_mode)
    return max(charged, item.minimum_charge_base_units)


# ---------------------------------------------------------------------------
# 1. calculate_tariff_charge
# ---------------------------------------------------------------------------

def calculate_tariff_charge(
    tariff: Tariff,
    metric_code: str,
    quantity: Decimal,
    unit: str,
    payer_account_id: int | None = None,
) -> list[ChargeDraft]:
    """
    Pure calculation — no DB writes.
    Returns one ChargeDraft per active TariffItem that matches metric_code.
    """
    from django.db.models import Q as _Q
    qs = tariff.active_items.filter(metric__code=metric_code)
    if unit:
        qs = qs.filter(_Q(unit__isnull=True) | _Q(unit__code=unit))
    items = qs.select_related("metric", "charged_asset", "receiving_account", "unit")
    drafts = []
    for item in items:
        amount = _calculate_charge(item, Decimal(str(quantity)))
        drafts.append(ChargeDraft(
            tariff_item=item,
            quantity=Decimal(str(quantity)),
            unit=unit,
            amount_base_units=amount,
            payer_account_id=payer_account_id or 0,
            receiving_account_id=item.receiving_account_id,
            asset_id=item.charged_asset_id,
            metadata={
                "tariff_id": tariff.pk,
                "tariff_code": tariff.code,
                "item_id": item.pk,
                "item_code": item.metric.code,
                "metric_code": metric_code,
                "quantity": str(quantity),
                "unit": unit or "",
            },
        ))
    return drafts


# ---------------------------------------------------------------------------
# 2. check_can_afford
# ---------------------------------------------------------------------------

def check_can_afford(
    tariff: Tariff,
    payer_account,
    charges: list[tuple[str, Decimal, str]],
) -> tuple[bool, str]:
    """
    Read-only balance check for a batch of charges.

    charges: list of (metric_code, quantity, unit) — all are evaluated together
             so shared-asset totals are combined before comparing to the balance.

    Returns (True, "") if the payer can cover all charges.
    Returns (False, human_readable_error) if any asset balance is short.
    No DB writes — safe to call before saving any resource.
    """
    debit_totals: dict[tuple[int, int], int] = {}  # (account_pk, asset_pk) -> base_units

    for metric_code, quantity, unit in charges:
        drafts = calculate_tariff_charge(
            tariff, metric_code, Decimal(str(quantity)), unit,
            payer_account_id=payer_account.pk,
        )
        for draft in drafts:
            key = (payer_account.pk, draft.asset_id)
            debit_totals[key] = debit_totals.get(key, 0) + draft.amount_base_units

    if not debit_totals:
        return True, ""  # no matching tariff items → no charge → allow

    from toto.assets.models import Asset
    for (account_id, asset_id), needed in debit_totals.items():
        holding = AssetHolding.objects.filter(
            account_id=account_id, asset_id=asset_id
        ).first()
        balance = holding.balance_base_units if holding else 0
        if balance < needed:
            asset = Asset.objects.get(pk=asset_id)
            return False, (
                f"Insufficient {asset.unit_name}: "
                f"need {from_base_units(needed, asset.decimals)}, "
                f"have {from_base_units(balance, asset.decimals)}."
            )

    return True, ""


# ---------------------------------------------------------------------------
# 3. rate_usage_record
# ---------------------------------------------------------------------------

def rate_usage_record(usage_record: UsageRecord) -> list[UsageCharge]:
    """
    Calculate charges for a UsageRecord and persist UsageCharge rows.
    Marks the record RATED. Idempotent if already rated.
    """
    if usage_record.status == UsageStatus.RATED:
        return list(usage_record.charges.all())
    if usage_record.status == UsageStatus.POSTED:
        raise ValueError("Cannot re-rate a posted UsageRecord.")

    drafts = calculate_tariff_charge(
        tariff=usage_record.tariff,
        metric_code=usage_record.metric_code,
        quantity=usage_record.quantity,
        unit=usage_record.unit,
        payer_account_id=usage_record.payer_account_id,
    )

    with transaction.atomic():
        usage_record.charges.all().delete()
        charges = []
        for draft in drafts:
            charge = UsageCharge.objects.create(
                usage_record=usage_record,
                tariff_item=draft.tariff_item,
                charged_asset_id=draft.asset_id,
                quantity=draft.quantity,
                unit=draft.unit,
                price_per_unit_base_units=draft.tariff_item.price_per_unit_base_units,
                amount_base_units=draft.amount_base_units,
                payer_account=usage_record.payer_account,
                receiving_account_id=draft.receiving_account_id,
                metadata=draft.metadata,
            )
            charges.append(charge)
        usage_record.status = UsageStatus.RATED
        usage_record.rated_at = timezone.now()
        usage_record.save(update_fields=["status", "rated_at", "updated_at"])
    return charges


# ---------------------------------------------------------------------------
# 3. post_usage_record
# ---------------------------------------------------------------------------

def post_usage_record(
    usage_record: UsageRecord,
    reference: str | None = None,
    description: str | None = None,
    pre_post_hook=None,
) -> LedgerTransaction:
    """
    Drain payer holdings and credit receiving accounts via immutable ledger.
    Atomic and idempotent: if the usage_record is already POSTED, returns
    the existing transaction.
    Raises ValueError if insufficient funds.
    """
    if usage_record.status == UsageStatus.POSTED:
        if usage_record.ledger_transaction_id:
            return usage_record.ledger_transaction
        raise ValueError("UsageRecord marked posted but has no ledger transaction.")

    if usage_record.status not in (UsageStatus.PENDING, UsageStatus.RATED, UsageStatus.FAILED):
        raise ValueError(f"Cannot post a UsageRecord in status '{usage_record.status}'.")

    ref = reference or f"tariff-usage-{usage_record.uuid}"
    desc = description or (
        f"Tariff {usage_record.tariff.code} / "
        f"{usage_record.metric_code} x {usage_record.quantity} {usage_record.unit}"
    )

    # Idempotency: if the ledger transaction already exists, use it.
    try:
        existing_tx = LedgerTransaction.objects.get(reference=ref)
        if existing_tx.posted:
            usage_record.status = UsageStatus.POSTED
            usage_record.ledger_transaction = existing_tx
            usage_record.save(update_fields=["status", "ledger_transaction", "updated_at"])
            return existing_tx
    except LedgerTransaction.DoesNotExist:
        pass

    # Ensure charges are rated
    if usage_record.status in (UsageStatus.PENDING, UsageStatus.FAILED):
        rate_usage_record(usage_record)
        usage_record.refresh_from_db()

    charges = list(
        usage_record.charges.select_related("charged_asset", "payer_account", "receiving_account")
    )
    if not charges:
        usage_record.status = UsageStatus.FAILED
        usage_record.error_message = "No matching tariff items found."
        usage_record.save(update_fields=["status", "error_message", "updated_at"])
        raise ValueError("No matching tariff items found for this usage record.")

    # Group charges by (payer_account, asset): total debit needed
    debit_totals: dict[tuple[int, int], int] = {}
    for charge in charges:
        key = (charge.payer_account_id, charge.charged_asset_id)
        debit_totals[key] = debit_totals.get(key, 0) + charge.amount_base_units

    _insufficient_error: str | None = None

    try:
        with transaction.atomic():
            # Lock holdings for update to prevent race conditions
            holding_map: dict[tuple[int, int], AssetHolding] = {}
            for (account_id, asset_id), _ in debit_totals.items():
                holding, _ = AssetHolding.objects.select_for_update().get_or_create(
                    account_id=account_id,
                    asset_id=asset_id,
                    defaults={"balance_base_units": 0},
                )
                holding_map[(account_id, asset_id)] = holding

            # Check balances inside lock; raise to abort the atomic block cleanly
            for (account_id, asset_id), needed in debit_totals.items():
                holding = holding_map[(account_id, asset_id)]
                if holding.balance_base_units < needed:
                    from toto.assets.models import Asset, LedgerAccount as _LA
                    asset_obj = Asset.objects.get(pk=asset_id)
                    account_obj = _LA.objects.get(pk=account_id)
                    shortfall = from_base_units(needed - holding.balance_base_units, asset_obj.decimals)
                    _insufficient_error = (
                        f"Insufficient {asset_obj.unit_name} balance in account {account_obj.code}: "
                        f"need {from_base_units(needed, asset_obj.decimals)}, "
                        f"have {holding.balance_display}, "
                        f"short by {shortfall}."
                    )
                    raise ValueError(_insufficient_error)

        # Build metadata for the transaction
        tx_metadata = {
            "usage_record_uuid": str(usage_record.uuid),
            "tariff_id": usage_record.tariff_id,
            "tariff_code": usage_record.tariff.code,
            "metric_code": usage_record.metric_code,
            "quantity": str(usage_record.quantity),
            "unit": usage_record.unit,
            "charge_count": len(charges),
        }

        tx = LedgerTransaction.objects.create(
            reference=ref,
            transaction_type=TransactionType.ASSET_TRANSFER,
            description=desc,
            source_type="tariff_usage",
            source_id=str(usage_record.uuid),
            metadata=tx_metadata,
        )

        # Write ledger entries and update holdings
        for (account_id, asset_id), needed in debit_totals.items():
            LedgerEntry.objects.create(
                transaction=tx,
                account_id=account_id,
                asset_id=asset_id,
                amount_base_units=-needed,
            )
            holding = holding_map[(account_id, asset_id)]
            holding.balance_base_units -= needed
            holding.save(update_fields=["balance_base_units", "updated_at"])

        # Credit receiving accounts
        credit_totals: dict[tuple[int, int], int] = {}
        for charge in charges:
            key = (charge.receiving_account_id, charge.charged_asset_id)
            credit_totals[key] = credit_totals.get(key, 0) + charge.amount_base_units

        for (account_id, asset_id), amount in credit_totals.items():
            LedgerEntry.objects.create(
                transaction=tx,
                account_id=account_id,
                asset_id=asset_id,
                amount_base_units=amount,
            )
            recv_holding, _ = AssetHolding.objects.select_for_update().get_or_create(
                account_id=account_id,
                asset_id=asset_id,
                defaults={"balance_base_units": 0},
            )
            recv_holding.balance_base_units += amount
            recv_holding.save(update_fields=["balance_base_units", "updated_at"])

        # Allow a caller to attach signing metadata before the tx is frozen.
        if pre_post_hook:
            try:
                pre_post_hook(tx)
            except Exception as _hook_exc:
                import logging as _log
                _log.getLogger(__name__).warning("billing: pre_post_hook failed: %s", _hook_exc)

        post_fields = ["posted"]
        if tx.signature:
            post_fields += ["signature", "payload_hash", "nonce", "idempotency_key", "signed_at"]
        if tx.signed_by_key_id:
            post_fields.append("signed_by_key")
        if tx.authorization_id:
            post_fields.append("authorization")

        tx.posted = True
        tx.save(update_fields=post_fields)

        usage_record.status = UsageStatus.POSTED
        usage_record.ledger_transaction = tx
        usage_record.save(update_fields=["status", "ledger_transaction", "updated_at"])

    except ValueError:
        # Atomic block rolled back; persist FAILED status outside it so the save is not lost
        if _insufficient_error:
            usage_record.status = UsageStatus.FAILED
            usage_record.error_message = _insufficient_error
            usage_record.save(update_fields=["status", "error_message", "updated_at"])
        raise

    return tx


# ---------------------------------------------------------------------------
# 4. record_and_post_usage
# ---------------------------------------------------------------------------

def record_and_post_usage(
    tariff: Tariff,
    payer_account,
    metric_code: str,
    quantity: Decimal,
    unit: str,
    source_type: str = "",
    source_id: str = "",
    metadata: dict | None = None,
    reference: str | None = None,
    description: str | None = None,
    pre_post_hook=None,
) -> tuple[UsageRecord, LedgerTransaction]:
    """Create a UsageRecord and immediately post it."""
    record = UsageRecord.objects.create(
        tariff=tariff,
        payer_account=payer_account,
        metric_code=metric_code,
        quantity=Decimal(str(quantity)),
        unit=unit,
        source_type=source_type,
        source_id=source_id,
        occurred_at=timezone.now(),
        metadata=metadata or {},
    )
    tx = post_usage_record(record, reference=reference, description=description, pre_post_hook=pre_post_hook)
    record.refresh_from_db()
    return record, tx


# ---------------------------------------------------------------------------
# 5. simulate_tariff
# ---------------------------------------------------------------------------

def simulate_tariff(
    tariff: Tariff,
    usage_payload: list[dict],
) -> dict:
    """
    Simulate charges without any DB writes.

    usage_payload: list of {"metric_code": str, "quantity": Decimal|str, "unit": str}

    Returns:
    {
        "lines": [{"metric_code", "quantity", "unit", "item_code", "asset", "amount_base_units", "amount_display"}, ...],
        "totals_by_asset": {"UNIT_NAME": {"base_units": int, "display": Decimal}},
        "total_items": int,
    }
    """
    lines = []
    totals: dict[str, dict] = {}

    for entry in usage_payload:
        metric_code = entry["metric_code"]
        quantity = Decimal(str(entry["quantity"]))
        unit = entry.get("unit", "")

        drafts = calculate_tariff_charge(tariff, metric_code, quantity, unit)
        for draft in drafts:
            asset = draft.tariff_item.charged_asset
            display = from_base_units(draft.amount_base_units, asset.decimals)
            lines.append({
                "metric_code": metric_code,
                "quantity": quantity,
                "unit": unit,
                "item_code": draft.tariff_item.metric.code,
                "item_name": draft.tariff_item.name,
                "asset": asset.unit_name,
                "amount_base_units": draft.amount_base_units,
                "amount_display": display,
                "price_per_unit_display": draft.tariff_item.price_per_unit_display,
                "unit_quantity": draft.tariff_item.unit_quantity,
            })
            if asset.unit_name not in totals:
                totals[asset.unit_name] = {"base_units": 0, "display": Decimal("0")}
            totals[asset.unit_name]["base_units"] += draft.amount_base_units
            totals[asset.unit_name]["display"] += display

    return {
        "lines": lines,
        "totals_by_asset": totals,
        "total_items": len(lines),
    }
