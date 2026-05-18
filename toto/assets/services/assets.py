from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from toto.assets.hashing import attach_hash
from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    TransactionType,
    to_base_units,
)


def _get_system_issuance_account() -> LedgerAccount:
    account, _ = LedgerAccount.objects.get_or_create(
        code="system_issuance",
        defaults={
            "name": "System Issuance",
            "account_type": AccountType.SYSTEM,
            "active": True,
        },
    )
    return account


def create_asset(
    *,
    name: str,
    unit_name: str,
    total_supply: Decimal,
    decimals: int,
    reserve_account: LedgerAccount,
    reference: str,
    description: str = "",
    metadata=None,
) -> Asset:
    with transaction.atomic():
        if total_supply <= 0:
            raise ValidationError("total_supply must be positive.")
        if not (0 <= decimals <= 19):
            raise ValidationError("decimals must be between 0 and 19.")

        total_supply_base = to_base_units(total_supply, decimals)

        asset = Asset.objects.create(
            name=name,
            unit_name=unit_name,
            decimals=decimals,
            total_supply_base_units=total_supply_base,
            active=True,
            metadata=metadata,
        )

        system_account = _get_system_issuance_account()

        reserve_holding, _ = AssetHolding.objects.get_or_create(
            asset=asset,
            account=reserve_account,
            defaults={"balance_base_units": 0},
        )
        reserve_holding.balance_base_units += total_supply_base
        reserve_holding.save(update_fields=["balance_base_units", "updated_at"])

        tx = LedgerTransaction.objects.create(
            reference=reference,
            transaction_type=TransactionType.ASSET_CREATE,
            description=description,
            asset=asset,
            metadata=metadata,
        )

        LedgerEntry.objects.create(
            transaction=tx,
            account=system_account,
            asset=asset,
            amount_base_units=-total_supply_base,
        )
        LedgerEntry.objects.create(
            transaction=tx,
            account=reserve_account,
            asset=asset,
            amount_base_units=total_supply_base,
        )

        tx.posted = True
        tx.save(update_fields=["posted"])

        attach_hash(tx)

        return asset


def transfer_asset(
    *,
    asset: Asset,
    sender_account: LedgerAccount,
    receiver_account: LedgerAccount,
    amount: Decimal,
    reference: str,
    description: str = "",
    metadata=None,
) -> LedgerTransaction:
    with transaction.atomic():
        amount_base = to_base_units(amount, asset.decimals)

        if amount_base <= 0:
            raise ValidationError("Transfer amount must be positive.")
        if not asset.active:
            raise ValidationError("Asset is not active.")
        if not sender_account.active:
            raise ValidationError("Sender account is not active.")
        if not receiver_account.active:
            raise ValidationError("Receiver account is not active.")

        holdings = AssetHolding.objects.select_for_update().filter(
            asset=asset,
            account__in=[sender_account, receiver_account],
        )
        holding_map = {h.account_id: h for h in holdings}

        sender_holding = holding_map.get(sender_account.pk)
        if sender_holding is None or sender_holding.balance_base_units < amount_base:
            raise ValidationError("Insufficient balance.")

        if receiver_account.pk not in holding_map:
            receiver_holding = AssetHolding.objects.create(
                asset=asset,
                account=receiver_account,
                balance_base_units=0,
            )
        else:
            receiver_holding = holding_map[receiver_account.pk]

        tx = LedgerTransaction.objects.create(
            reference=reference,
            transaction_type=TransactionType.ASSET_TRANSFER,
            description=description,
            asset=asset,
            metadata=metadata,
        )

        LedgerEntry.objects.create(
            transaction=tx,
            account=sender_account,
            asset=asset,
            amount_base_units=-amount_base,
        )
        LedgerEntry.objects.create(
            transaction=tx,
            account=receiver_account,
            asset=asset,
            amount_base_units=amount_base,
        )

        sender_holding.balance_base_units -= amount_base
        sender_holding.save(update_fields=["balance_base_units", "updated_at"])

        receiver_holding.balance_base_units += amount_base
        receiver_holding.save(update_fields=["balance_base_units", "updated_at"])

        tx.posted = True
        tx.save(update_fields=["posted"])

        attach_hash(tx)

        return tx


def reverse_transaction(
    *,
    transaction: LedgerTransaction,
    reference: str,
    description: str = "",
    metadata=None,
) -> LedgerTransaction:
    from django.db import transaction as db_transaction

    with db_transaction.atomic():
        original = LedgerTransaction.objects.select_for_update().get(pk=transaction.pk)

        if not original.posted:
            raise ValidationError("Only posted transactions can be reversed.")
        if original.reversal_set.exists():
            raise ValidationError("This transaction has already been reversed.")

        original_entries = list(original.entries.select_related("account", "asset").all())

        reversal_tx = LedgerTransaction.objects.create(
            reference=reference,
            transaction_type=TransactionType.REVERSAL,
            description=description,
            asset=original.asset,
            reversed_transaction=original,
            metadata=metadata,
        )

        account_ids = [e.account_id for e in original_entries]
        asset_id = original_entries[0].asset_id if original_entries else None

        holdings = {}
        if asset_id:
            for holding in AssetHolding.objects.select_for_update().filter(
                asset_id=asset_id, account_id__in=account_ids
            ):
                holdings[holding.account_id] = holding

        for entry in original_entries:
            LedgerEntry.objects.create(
                transaction=reversal_tx,
                account=entry.account,
                asset=entry.asset,
                amount_base_units=-entry.amount_base_units,
            )

            holding = holdings.get(entry.account_id)
            if holding is not None:
                holding.balance_base_units -= entry.amount_base_units
                holding.save(update_fields=["balance_base_units", "updated_at"])

        reversal_tx.posted = True
        reversal_tx.save(update_fields=["posted"])

        attach_hash(reversal_tx)

        return reversal_tx


def get_stablecoin_for_currency(currency_code: str):
    """Return the pegged Asset for a fiat currency code, or None."""
    from toto.assets.models import Currency
    try:
        return Currency.objects.select_related('asset').get(
            code__iexact=currency_code,
            is_active=True,
            asset__active=True,
        ).asset
    except Currency.DoesNotExist:
        return None


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
    from toto.assets.models import Obligation, ObligationStatus
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
    from toto.assets.models import Obligation, ObligationStatus
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
