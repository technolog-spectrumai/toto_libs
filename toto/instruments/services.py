from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.assets.backend import get_backend
from toto.assets.models import Obligation, ObligationStatus, from_base_units, to_base_units

from .models import (
    AmortizationContract,
    AmortizationEntry,
    AmortizationStatus,
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
    OptionContract,
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


class OptionService:
    @staticmethod
    @transaction.atomic
    def exercise(option: OptionContract, *, reference: str | None = None):
        instrument = option.instrument
        if instrument.status != InstrumentStatus.ACTIVE:
            raise ValidationError("Only active options can be exercised.")
        if option.is_expired:
            raise ValidationError("Option has expired.")
        if option.is_exercised:
            raise ValidationError("Option has already been exercised.")
        tx = get_backend().transfer_asset(
            asset=option.underlying_asset,
            sender_account=option.writer_account,
            receiver_account=option.buyer_account,
            amount=option.quantity_display,
            reference=reference or f"{instrument.reference}-OPTION-EXERCISE",
            description=f"Exercise option {instrument.reference}",
            metadata={"instrument": instrument.reference, "action": "option_exercise"},
        )
        option.exercised_at = timezone.now()
        instrument.status = InstrumentStatus.SETTLED
        option.save(update_fields=["exercised_at", "updated_at"])
        instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=instrument,
            action="option_exercise",
            status=InstrumentExecutionStatus.SUCCESS,
            transaction_obj=tx,
            result_data={"transaction_reference": tx.reference},
        )
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


class AmortizationService:
    @staticmethod
    @transaction.atomic
    def activate(contract: AmortizationContract):
        if contract.status != AmortizationStatus.DRAFT:
            raise ValidationError("Only draft contracts can be activated.")
        contract.status = AmortizationStatus.ACTIVE
        contract.instrument.status = InstrumentStatus.ACTIVE
        contract.save(update_fields=["status", "updated_at"])
        contract.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=contract.instrument,
            action="amortization_activate",
            status=InstrumentExecutionStatus.SUCCESS,
            result_data={"status": contract.status},
        )

    @staticmethod
    @transaction.atomic
    def resume(contract: AmortizationContract):
        if contract.status != AmortizationStatus.PAUSED:
            raise ValidationError("Only paused contracts can be resumed.")
        contract.status = AmortizationStatus.ACTIVE
        contract.instrument.status = InstrumentStatus.ACTIVE
        contract.save(update_fields=["status", "updated_at"])
        contract.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=contract.instrument,
            action="amortization_resume",
            status=InstrumentExecutionStatus.SUCCESS,
        )

    @staticmethod
    @transaction.atomic
    def pause(contract: AmortizationContract):
        if contract.status != AmortizationStatus.ACTIVE:
            raise ValidationError("Only active contracts can be paused.")
        contract.status = AmortizationStatus.PAUSED
        contract.instrument.status = InstrumentStatus.PAUSED
        contract.save(update_fields=["status", "updated_at"])
        contract.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=contract.instrument,
            action="amortization_pause",
            status=InstrumentExecutionStatus.SUCCESS,
        )

    @staticmethod
    @transaction.atomic
    def cancel(contract: AmortizationContract):
        if contract.status in (AmortizationStatus.EXHAUSTED, AmortizationStatus.CANCELLED):
            raise ValidationError("Contract is already exhausted or cancelled.")
        contract.status = AmortizationStatus.CANCELLED
        contract.instrument.status = InstrumentStatus.CANCELLED
        contract.save(update_fields=["status", "updated_at"])
        contract.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=contract.instrument,
            action="amortization_cancel",
            status=InstrumentExecutionStatus.SUCCESS,
        )

    @staticmethod
    def can_amortize(contract: AmortizationContract, amount_base_units: int) -> bool:
        return (
            contract.status == AmortizationStatus.ACTIVE
            and amount_base_units > 0
            and amount_base_units <= contract.remaining_amount_base_units
        )

    @staticmethod
    @transaction.atomic
    def mark_exhausted(contract: AmortizationContract):
        contract.status = AmortizationStatus.EXHAUSTED
        contract.instrument.status = InstrumentStatus.SETTLED
        contract.save(update_fields=["status", "updated_at"])
        contract.instrument.save(update_fields=["status", "updated_at"])
        record_execution(
            instrument=contract.instrument,
            action="amortization_exhausted",
            status=InstrumentExecutionStatus.SUCCESS,
        )

    @staticmethod
    @transaction.atomic
    def amortize(
        contract: AmortizationContract,
        amount_base_units: int,
        source_type: str = "",
        source_id: str = "",
        metadata: dict | None = None,
    ) -> AmortizationEntry:
        if contract.status != AmortizationStatus.ACTIVE:
            raise ValidationError("Contract must be active to amortize.")
        if amount_base_units <= 0:
            raise ValidationError("Amount must be positive.")
        if amount_base_units > contract.remaining_amount_base_units:
            raise ValidationError(
                f"Amount ({amount_base_units}) exceeds remaining balance "
                f"({contract.remaining_amount_base_units})."
            )

        amount = from_base_units(amount_base_units, contract.asset.decimals)
        ref = f"{contract.instrument.reference}-AMORT-{contract.amortized_amount_base_units + amount_base_units}"
        tx = get_backend().transfer_asset(
            asset=contract.asset,
            sender_account=contract.source_account,
            receiver_account=contract.destination_account,
            amount=amount,
            reference=ref,
            description=f"Amortize {contract.instrument.reference}",
            metadata={
                "instrument": contract.instrument.reference,
                "action": "amortize",
                "source_type": source_type,
                "source_id": source_id,
            },
        )

        entry = AmortizationEntry.objects.create(
            contract=contract,
            amount_base_units=amount_base_units,
            source_type=source_type,
            source_id=source_id,
            transaction=tx,
            metadata=metadata or {},
        )

        contract.amortized_amount_base_units += amount_base_units
        contract.save(update_fields=["amortized_amount_base_units", "updated_at"])

        record_execution(
            instrument=contract.instrument,
            action="amortize",
            status=InstrumentExecutionStatus.SUCCESS,
            transaction_obj=tx,
            result_data={
                "entry_id": entry.pk,
                "amount_base_units": amount_base_units,
                "remaining": contract.remaining_amount_base_units,
            },
        )

        if contract.is_exhausted:
            AmortizationService.mark_exhausted(contract)

        return entry
