from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.assets.backend import get_backend
from toto.assets.models import Obligation, ObligationStatus, to_base_units

from .models import (
    EscrowContract,
    EscrowStatus,
    FinancialInstrument,
    ForwardContract,
    InstrumentExecution,
    InstrumentExecutionStatus,
    InstrumentObligation,
    InstrumentObligationRole,
    InstrumentStatus,
    InstrumentType,
    TimelockContract,
    VestingContract,
    StakingPosition,
)


def _maybe_obligation_kwargs(*, source_type: str, source_id: str, metadata: dict | None = None) -> dict:
    """Return optional Obligation kwargs only when the assets model supports them."""
    field_names = {field.name for field in Obligation._meta.get_fields()}
    kwargs = {}
    if "source_type" in field_names:
        kwargs["source_type"] = source_type
    if "source_id" in field_names:
        kwargs["source_id"] = source_id
    if "metadata" in field_names:
        kwargs["metadata"] = metadata or {}
    return kwargs


def record_execution(*, instrument, action, status, transaction_obj=None, input_data=None, result_data=None, error_message=""):
    return InstrumentExecution.objects.create(
        instrument=instrument,
        action=action,
        input_data=input_data or {},
        result_data=result_data or {},
        transaction=transaction_obj,
        status=status,
        error_message=error_message,
    )


class EscrowService:
    @staticmethod
    @transaction.atomic
    def fund(escrow: EscrowContract, *, reference: str | None = None):
        escrow.full_clean()
        if escrow.status != EscrowStatus.DRAFT:
            raise ValidationError("Only draft escrows can be funded.")

        tx = get_backend().transfer_asset(
            asset=escrow.asset,
            sender_account=escrow.buyer_account,
            receiver_account=escrow.escrow_account,
            amount=escrow.amount_display,
            reference=reference or f"{escrow.instrument.reference}-ESCROW-FUND",
            description=f"Fund escrow {escrow.instrument.reference}",
            metadata={"instrument": escrow.instrument.reference, "action": "escrow_fund"},
        )
        escrow.status = EscrowStatus.FUNDED
        escrow.funded_at = timezone.now()
        escrow.instrument.status = InstrumentStatus.ACTIVE
        escrow.save(update_fields=["status", "funded_at", "updated_at"])
        escrow.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=escrow.instrument,
            action="escrow_fund",
            status=InstrumentExecutionStatus.SUCCESS,
            transaction_obj=tx,
            result_data={"transaction_reference": tx.reference},
        )
        return tx

    @staticmethod
    @transaction.atomic
    def release(escrow: EscrowContract, *, reference: str | None = None):
        if escrow.status != EscrowStatus.FUNDED:
            raise ValidationError("Only funded escrows can be released.")
        tx = get_backend().transfer_asset(
            asset=escrow.asset,
            sender_account=escrow.escrow_account,
            receiver_account=escrow.seller_account,
            amount=escrow.amount_display,
            reference=reference or f"{escrow.instrument.reference}-ESCROW-RELEASE",
            description=f"Release escrow {escrow.instrument.reference}",
            metadata={"instrument": escrow.instrument.reference, "action": "escrow_release"},
        )
        escrow.status = EscrowStatus.RELEASED
        escrow.released_at = timezone.now()
        escrow.instrument.status = InstrumentStatus.SETTLED
        escrow.save(update_fields=["status", "released_at", "updated_at"])
        escrow.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=escrow.instrument,
            action="escrow_release",
            status=InstrumentExecutionStatus.SUCCESS,
            transaction_obj=tx,
            result_data={"transaction_reference": tx.reference},
        )
        return tx

    @staticmethod
    @transaction.atomic
    def refund(escrow: EscrowContract, *, reference: str | None = None):
        if escrow.status not in {EscrowStatus.FUNDED, EscrowStatus.DISPUTED}:
            raise ValidationError("Only funded or disputed escrows can be refunded.")
        tx = get_backend().transfer_asset(
            asset=escrow.asset,
            sender_account=escrow.escrow_account,
            receiver_account=escrow.buyer_account,
            amount=escrow.amount_display,
            reference=reference or f"{escrow.instrument.reference}-ESCROW-REFUND",
            description=f"Refund escrow {escrow.instrument.reference}",
            metadata={"instrument": escrow.instrument.reference, "action": "escrow_refund"},
        )
        escrow.status = EscrowStatus.REFUNDED
        escrow.refunded_at = timezone.now()
        escrow.instrument.status = InstrumentStatus.CANCELLED
        escrow.save(update_fields=["status", "refunded_at", "updated_at"])
        escrow.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=escrow.instrument,
            action="escrow_refund",
            status=InstrumentExecutionStatus.SUCCESS,
            transaction_obj=tx,
            result_data={"transaction_reference": tx.reference},
        )
        return tx

    @staticmethod
    @transaction.atomic
    def dispute(escrow: EscrowContract, *, reason: str = ""):
        if escrow.status != EscrowStatus.FUNDED:
            raise ValidationError("Only funded escrows can be disputed.")
        escrow.status = EscrowStatus.DISPUTED
        escrow.disputed_at = timezone.now()
        escrow.save(update_fields=["status", "disputed_at", "updated_at"])
        record_execution(
            instrument=escrow.instrument,
            action="escrow_dispute",
            status=InstrumentExecutionStatus.SUCCESS,
            input_data={"reason": reason},
        )
        return escrow


class ForwardService:
    @staticmethod
    @transaction.atomic
    def activate(forward: ForwardContract):
        instrument = forward.instrument
        if instrument.status != InstrumentStatus.DRAFT:
            raise ValidationError("Only draft forward contracts can be activated.")

        seller_delivery = Obligation.objects.create(
            reference=f"{instrument.reference}-DELIVERY",
            debtor_account=forward.seller_account,
            creditor_account=forward.buyer_account,
            asset=forward.underlying_asset,
            amount_base_units=forward.quantity_base_units,
            due_at=forward.settlement_at,
            **_maybe_obligation_kwargs(
                source_type="forward_contract",
                source_id=instrument.reference,
                metadata={"role": "underlying_delivery"},
            ),
        )
        buyer_payment = Obligation.objects.create(
            reference=f"{instrument.reference}-PAYMENT",
            debtor_account=forward.buyer_account,
            creditor_account=forward.seller_account,
            asset=forward.payment_asset,
            amount_base_units=forward.payment_amount_base_units,
            due_at=forward.settlement_at,
            **_maybe_obligation_kwargs(
                source_type="forward_contract",
                source_id=instrument.reference,
                metadata={"role": "payment"},
            ),
        )
        InstrumentObligation.objects.create(
            instrument=instrument,
            obligation=seller_delivery,
            role=InstrumentObligationRole.UNDERLYING_DELIVERY,
        )
        InstrumentObligation.objects.create(
            instrument=instrument,
            obligation=buyer_payment,
            role=InstrumentObligationRole.PAYMENT,
        )
        instrument.status = InstrumentStatus.ACTIVE
        instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=instrument,
            action="activate_forward",
            status=InstrumentExecutionStatus.SUCCESS,
            result_data={
                "seller_delivery_obligation": seller_delivery.reference,
                "buyer_payment_obligation": buyer_payment.reference,
            },
        )
        return seller_delivery, buyer_payment


class TimelockService:
    @staticmethod
    @transaction.atomic
    def fund(timelock: TimelockContract, *, reference: str | None = None):
        if timelock.instrument.status != InstrumentStatus.DRAFT:
            raise ValidationError("Only draft timelocks can be funded.")
        tx = get_backend().transfer_asset(
            asset=timelock.asset,
            sender_account=timelock.owner_account,
            receiver_account=timelock.instrument.contract_account,
            amount=timelock.amount_display,
            reference=reference or f"{timelock.instrument.reference}-TIMELOCK-FUND",
            description=f"Fund timelock {timelock.instrument.reference}",
            metadata={"instrument": timelock.instrument.reference, "action": "timelock_fund"},
        )
        timelock.instrument.status = InstrumentStatus.ACTIVE
        timelock.instrument.save(update_fields=["status", "updated_at"])
        record_execution(instrument=timelock.instrument, action="timelock_fund", status=InstrumentExecutionStatus.SUCCESS, transaction_obj=tx)
        return tx

    @staticmethod
    @transaction.atomic
    def release(timelock: TimelockContract, *, reference: str | None = None):
        if not timelock.is_unlocked:
            raise ValidationError("Timelock is not unlocked yet.")
        if timelock.released_at:
            raise ValidationError("Timelock has already been released.")
        tx = get_backend().transfer_asset(
            asset=timelock.asset,
            sender_account=timelock.instrument.contract_account,
            receiver_account=timelock.beneficiary_account,
            amount=timelock.amount_display,
            reference=reference or f"{timelock.instrument.reference}-TIMELOCK-RELEASE",
            description=f"Release timelock {timelock.instrument.reference}",
            metadata={"instrument": timelock.instrument.reference, "action": "timelock_release"},
        )
        timelock.released_at = timezone.now()
        timelock.instrument.status = InstrumentStatus.SETTLED
        timelock.save(update_fields=["released_at", "updated_at"])
        timelock.instrument.save(update_fields=["status", "updated_at"])
        record_execution(instrument=timelock.instrument, action="timelock_release", status=InstrumentExecutionStatus.SUCCESS, transaction_obj=tx)
        return tx


class StakingService:
    @staticmethod
    @transaction.atomic
    def stake(position: StakingPosition, *, reference: str | None = None):
        if position.instrument.status != InstrumentStatus.DRAFT:
            raise ValidationError("Only draft staking positions can be staked.")
        amount = Decimal(position.staked_amount_base_units) / (Decimal(10) ** position.staked_asset.decimals)
        tx = get_backend().transfer_asset(
            asset=position.staked_asset,
            sender_account=position.staker_account,
            receiver_account=position.staking_account,
            amount=amount,
            reference=reference or f"{position.instrument.reference}-STAKE",
            description=f"Stake {position.instrument.reference}",
            metadata={"instrument": position.instrument.reference, "action": "stake"},
        )
        position.instrument.status = InstrumentStatus.ACTIVE
        position.instrument.save(update_fields=["status", "updated_at"])
        record_execution(instrument=position.instrument, action="stake", status=InstrumentExecutionStatus.SUCCESS, transaction_obj=tx)
        return tx

    @staticmethod
    @transaction.atomic
    def unstake(position: StakingPosition, *, reference: str | None = None):
        if position.unstaked_at:
            raise ValidationError("Position already unstaked.")
        if position.locked_until and timezone.now() < position.locked_until:
            raise ValidationError("Position is still locked.")
        amount = Decimal(position.staked_amount_base_units) / (Decimal(10) ** position.staked_asset.decimals)
        tx = get_backend().transfer_asset(
            asset=position.staked_asset,
            sender_account=position.staking_account,
            receiver_account=position.staker_account,
            amount=amount,
            reference=reference or f"{position.instrument.reference}-UNSTAKE",
            description=f"Unstake {position.instrument.reference}",
            metadata={"instrument": position.instrument.reference, "action": "unstake"},
        )
        position.unstaked_at = timezone.now()
        position.instrument.status = InstrumentStatus.SETTLED
        position.save(update_fields=["unstaked_at", "updated_at"])
        position.instrument.save(update_fields=["status", "updated_at"])
        record_execution(instrument=position.instrument, action="unstake", status=InstrumentExecutionStatus.SUCCESS, transaction_obj=tx)
        return tx
