"""Parked services — creating and fulfilling Obligations.

Pulled from ``toto.assets.services.assets`` on 2026-07-31 with the Obligation model.
``transfer_asset``/``to_base_units`` remain in the live app; imported here for
reference. Parked: not installed, not tested, not shipped.
"""
from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from toto.assets.models import Asset, LedgerAccount, to_base_units
from toto.assets.services.assets import transfer_asset

from .models import Obligation, ObligationStatus


def create_obligation(
    *,
    reference: str,
    debtor_account: LedgerAccount,
    creditor_account: LedgerAccount,
    asset: Asset,
    amount: Decimal,
    due_at,
    order_reference: str = "",
    collateral_account: LedgerAccount | None = None,
    collateral_asset: Asset | None = None,
    collateral_amount: Decimal = Decimal("0"),
):
    amount_base = to_base_units(amount, asset.decimals)
    if amount_base <= 0:
        raise ValidationError("Obligation amount must be positive.")
    collateral_base = 0
    if collateral_asset and collateral_amount > 0:
        collateral_base = to_base_units(collateral_amount, collateral_asset.decimals)
    return Obligation.objects.create(
        reference=reference,
        order_reference=order_reference,
        debtor_account=debtor_account,
        creditor_account=creditor_account,
        asset=asset,
        amount_base_units=amount_base,
        due_at=due_at,
        collateral_account=collateral_account,
        collateral_asset=collateral_asset,
        collateral_amount_base_units=collateral_base,
        status=ObligationStatus.PENDING,
    )


def fulfill_obligation(*, obligation, reference: str, description: str = ""):
    """Pay off an obligation: transfer asset from debtor to creditor, mark fulfilled."""
    with transaction.atomic():
        obligation = Obligation.objects.select_for_update().get(pk=obligation.pk)
        if obligation.status == ObligationStatus.FULFILLED:
            raise ValidationError("Obligation is already fulfilled.")
        tx = transfer_asset(
            asset=obligation.asset,
            sender_account=obligation.debtor_account,
            receiver_account=obligation.creditor_account,
            amount=obligation.amount_display,
            reference=reference,
            description=description or f"Fulfilling obligation {obligation.reference}",
        )
        from django.utils import timezone
        obligation.fulfilled_at = timezone.now()
        obligation.status = ObligationStatus.FULFILLED
        obligation.save(update_fields=["fulfilled_at", "status", "updated_at"])
        return tx
