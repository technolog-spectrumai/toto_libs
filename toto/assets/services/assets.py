from dataclasses import dataclass
from decimal import Decimal, ROUND_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.assets.hashing import attach_hash
from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    Tokenization,
    TokenizationDefaultReason,
    TokenizationStatus,
    TransactionType,
    from_base_units,
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


def distribute_asset(
    *,
    asset: Asset,
    recipient_account: LedgerAccount,
    amount: Decimal,
    reference: str,
    description: str = "",
    metadata=None,
    pre_post_hook=None,
) -> LedgerTransaction:
    """
    Transfer tokens from asset.reserve_account to recipient_account.
    This is the mechanism for admin-controlled distribution / user purchases.
    """
    if not asset.reserve_account_id:
        raise ValidationError("This asset has no reserve account configured.")
    return transfer_asset(
        asset=asset,
        sender_account=asset.reserve_account,
        receiver_account=recipient_account,
        amount=amount,
        reference=reference,
        description=description or f"Distribute {amount} {asset.unit_name} to {recipient_account.code}",
        metadata=metadata,
        pre_post_hook=pre_post_hook,
    )


def transfer_asset(
    *,
    asset: Asset,
    sender_account: LedgerAccount,
    receiver_account: LedgerAccount,
    amount: Decimal,
    reference: str,
    description: str = "",
    metadata=None,
    pre_post_hook=None,
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
        from django.apps import apps as _apps
        try:
            AssetFreeze = _apps.get_model('magistrate', 'AssetFreeze')
            if AssetFreeze.objects.filter(asset=asset, status='active').exists():
                raise ValidationError(
                    f"{asset.unit_name} is currently frozen by magistrate order and cannot be transferred."
                )
        except LookupError:
            pass

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

        if pre_post_hook:
            try:
                pre_post_hook(tx)
            except Exception:
                pass

        update_fields = ["posted"]
        signing_fields = ["signature", "payload_hash", "nonce", "idempotency_key", "signed_at"]
        for field in signing_fields:
            if getattr(tx, field, None):
                update_fields.append(field)

        tx.posted = True
        tx.save(update_fields=update_fields)

        attach_hash(tx)

        return tx


def default_tokenization(
    *,
    tokenization: Tokenization,
    reason: str,
    note: str = "",
    defaulted_by=None,
) -> Tokenization:
    if reason not in TokenizationDefaultReason.values:
        raise ValidationError("Choose a valid default reason.")

    with transaction.atomic():
        current = (
            Tokenization.objects
            .select_for_update()
            .select_related("asset")
            .get(pk=tokenization.pk)
        )

        if current.status == TokenizationStatus.DEFAULTED:
            raise ValidationError("This tokenization has already defaulted.")

        now = timezone.now()
        current.status = TokenizationStatus.DEFAULTED
        current.default_reason = reason
        current.default_note = note.strip()
        current.defaulted_at = now
        current.defaulted_by = defaulted_by
        current.save(update_fields=[
            "status",
            "default_reason",
            "default_note",
            "defaulted_at",
            "defaulted_by",
        ])

        asset = current.asset
        metadata = asset.metadata if isinstance(asset.metadata, dict) else {"previous_metadata": asset.metadata}
        metadata["tokenization_default"] = {
            "tokenization_id": current.pk,
            "reason": reason,
            "note": current.default_note,
            "defaulted_at": now.isoformat(),
        }
        asset.active = False
        asset.metadata = metadata
        asset.save(update_fields=["active", "metadata", "updated_at"])

        return current


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


@dataclass(frozen=True)
class ExchangeQuote:
    from_asset: Asset
    to_asset: Asset
    source_amount: Decimal
    converted_amount: Decimal
    commission_amount: Decimal
    gross_amount: Decimal
    rate: Decimal
    commission_percent: Decimal
    rate_source: str


def _display_quantum(asset: Asset) -> Decimal:
    return Decimal(1).scaleb(-asset.decimals)


def _round_display_amount(amount: Decimal, asset: Asset) -> Decimal:
    return Decimal(amount).quantize(_display_quantum(asset), rounding=ROUND_UP)


def get_exchange_rate(from_asset: Asset, to_asset: Asset):
    if from_asset.pk == to_asset.pk:
        return None, Decimal("1"), Decimal("0"), "same_asset"

    raise ValidationError(
        f"No automatic exchange rate from {from_asset.unit_name} to {to_asset.unit_name}. "
        "Open a bourse proposal instead."
    )


def quote_exchange(*, from_asset: Asset, to_asset: Asset, amount: Decimal) -> ExchangeQuote:
    if amount <= 0:
        raise ValidationError("Exchange amount must be positive.")

    _, rate, commission_percent, rate_source = get_exchange_rate(from_asset, to_asset)
    converted = _round_display_amount(Decimal(amount) * rate, to_asset)
    commission = _round_display_amount(converted * (commission_percent / Decimal("100")), to_asset)
    gross = converted + commission
    return ExchangeQuote(
        from_asset=from_asset,
        to_asset=to_asset,
        source_amount=Decimal(amount),
        converted_amount=converted,
        commission_amount=commission,
        gross_amount=gross,
        rate=rate,
        commission_percent=commission_percent,
        rate_source=rate_source,
    )


def quote_currency_payment(*, currency_code: str, payment_asset: Asset, amount: Decimal) -> ExchangeQuote:
    source_asset = get_stablecoin_for_currency(currency_code)
    if not source_asset:
        raise ValidationError(f"No asset is pegged to {currency_code}.")
    return quote_exchange(from_asset=source_asset, to_asset=payment_asset, amount=amount)


def get_exchange_fee_account() -> LedgerAccount:
    account, _ = LedgerAccount.objects.get_or_create(
        code="platform_exchange_fees",
        defaults={
            "name": "Platform Exchange Fees",
            "account_type": AccountType.SYSTEM,
            "active": True,
        },
    )
    return account


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
