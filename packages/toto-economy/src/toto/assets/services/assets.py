from dataclasses import dataclass
from decimal import Decimal, ROUND_UP

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from toto.assets.hashing import attach_hash
from toto.assets.models import (
    AccountType,
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
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
    code: str = "",
    symbol: str = "",
) -> Asset:
    """Issue a new asset. Master only, fixed supply, signed at birth.

    The authority check is the FIRST thing that happens: a branch has no
    business reaching this function at all, and refusing early means a refusal
    cannot leave half a ledger behind. Mirroring an asset the master issued is
    a different verb — see ``mirror_asset``.

    The whole supply is created here and never again. That is what makes it
    safe to commit the amount to the genesis hash.
    """
    from django.utils import timezone

    from toto.assets.currency_hash import build_genesis, compute_currency_hash
    from toto.assets.issuer import local_issuer, require_master

    require_master("issue assets")
    issuer = local_issuer()

    with transaction.atomic():
        if total_supply <= 0:
            raise ValidationError("total_supply must be positive.")
        if not (0 <= decimals <= 19):
            raise ValidationError("decimals must be between 0 and 19.")

        total_supply_base = to_base_units(total_supply, decimals)

        genesis = build_genesis(
            issuer_fingerprint=issuer.fingerprint,
            unit_name=unit_name,
            name=name,
            decimals=decimals,
            max_supply_base_units=total_supply_base,
            issued_at=timezone.now().isoformat(),
        )
        asset = Asset.objects.create(
            name=name,
            unit_name=unit_name,
            code=code,
            symbol=symbol,
            decimals=decimals,
            total_supply_base_units=total_supply_base,
            active=True,
            metadata=metadata,
            issuer=issuer,
            currency_hash=compute_currency_hash(genesis),
            genesis_payload=genesis,
            genesis_signature=issuer.sign_genesis(genesis),
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


def mirror_asset(*, genesis_payload: dict, signature: str,
                 issuer, origin_platform: str = "",
                 code: str = "", symbol: str = "") -> Asset:
    """Copy an asset the master issued into this platform's ledger.

    The ONLY writer of Asset rows on a branch. ``create_asset`` makes money;
    this copies a fact, and keeping them separate is what lets the first be
    refused wholesale on a branch without also breaking the second.

    Nothing is written until the signature verifies against the pinned issuer
    key: a mirror whose provenance cannot be checked is not a mirror, it is an
    assertion. Idempotent by hash, so re-importing a descriptor is harmless.
    """
    from toto.assets.currency_hash import compute_currency_hash

    if not issuer.verify_genesis(genesis_payload, signature):
        raise ValidationError(
            "This asset's genesis signature does not verify against the "
            f"issuer pinned here ({issuer.fingerprint[:12]}…). Refusing to "
            "mirror something whose origin cannot be checked.")
    if genesis_payload.get("issuer_fingerprint") != issuer.fingerprint:
        raise ValidationError(
            "The genesis document names a different issuer than the one that "
            "signed it.")

    digest = compute_currency_hash(genesis_payload)
    existing = Asset.objects.filter(currency_hash=digest).first()
    if existing is not None:
        return existing

    return Asset.objects.create(
        name=genesis_payload["name"],
        unit_name=genesis_payload["unit_name"],
        code=code,
        symbol=symbol,
        decimals=genesis_payload["decimals"],
        total_supply_base_units=genesis_payload["max_supply_base_units"],
        active=True,
        issuer=issuer,
        currency_hash=digest,
        genesis_payload=genesis_payload,
        genesis_signature=signature,
        origin_platform=origin_platform,
        is_mirror=True,
    )


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


class IdempotencyConflict(ValidationError):
    """The same reference was replayed with DIFFERENT parameters.

    A replay with identical parameters is a retry and returns the original
    transaction; the same key naming a different movement is the dangerous bug
    (a caller recycling references), and it must never silently do either thing.
    """


def _transfer_fingerprint(tx: LedgerTransaction) -> tuple:
    """The identity of a transfer, recomputed from its own entries.

    (asset id, sender account id, receiver account id, base amount) — enough to
    tell a retry from a reference collision without storing anything new.
    """
    debit = credit = None
    for entry in tx.entries.all():
        if entry.amount_base_units < 0:
            debit = entry
        elif entry.amount_base_units > 0:
            credit = entry
    return (
        tx.asset_id,
        debit.account_id if debit else None,
        credit.account_id if credit else None,
        credit.amount_base_units if credit else None,
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
    # Idempotent-return front door (Stripe semantics): a duplicate reference
    # with identical parameters is a retry — hand back the original instead of
    # an IntegrityError, which is what makes replayed bridge messages and
    # crash-recovery re-runs safe to apply blindly. The cheap lookup runs
    # before any locking or validation.
    existing = LedgerTransaction.objects.filter(reference=reference).first()
    if existing is not None:
        wanted = (asset.pk, sender_account.pk, receiver_account.pk,
                  to_base_units(amount, asset.decimals))
        if _transfer_fingerprint(existing) == wanted and existing.posted:
            return existing
        raise IdempotencyConflict(
            f"Reference '{reference}' already names a different transfer."
        )

    try:
        return _transfer_asset_locked(
            asset=asset,
            sender_account=sender_account,
            receiver_account=receiver_account,
            amount=amount,
            reference=reference,
            description=description,
            metadata=metadata,
            pre_post_hook=pre_post_hook,
        )
    except IntegrityError:
        # Two identical calls raced past the front door; one inserted, this one
        # hit the unique reference. Resolve it the same way a sequential
        # duplicate resolves.
        existing = LedgerTransaction.objects.filter(reference=reference).first()
        if existing is not None:
            wanted = (asset.pk, sender_account.pk, receiver_account.pk,
                      to_base_units(amount, asset.decimals))
            if _transfer_fingerprint(existing) == wanted and existing.posted:
                return existing
            raise IdempotencyConflict(
                f"Reference '{reference}' already names a different transfer."
            )
        raise


def _transfer_asset_locked(
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
        # A CLAIM account is the one place a negative balance is correct: the
        # units genuinely came from outside this database and the row records
        # what is owed for them. Opt-in per account, never by type — clearing's
        # vostro is EXTERNAL too, and its zero floor is its credit control.
        from_outside = sender_account.allows_negative
        if not from_outside and (
                sender_holding is None
                or sender_holding.balance_base_units < amount_base):
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

        if sender_holding is None:
            # Only reachable for an EXTERNAL sender: the first movement out of
            # a claim account has no prior holding row to decrement.
            sender_holding, _ = AssetHolding.objects.get_or_create(
                asset=asset, account=sender_account,
                defaults={"balance_base_units": 0})
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


def get_currency_asset(currency_code: str):
    """Return the Asset carrying this display code, or None.

    The retired Currency model held ``code`` on a row of its own; it now lives
    on the asset. A lookup by code is still a lookup by LABEL, so it is for
    display and legacy call sites only — never for identity, which is the
    genesis hash, and never for "what do we bill in", which is the contract.
    """
    from toto.assets.models import Asset

    return Asset.objects.filter(code__iexact=currency_code, active=True).first()


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


def _require_trading_host() -> None:
    """Trading happens on the master, and nowhere else.

    The exchange services ship in the wheel to every host that installs the
    ledger, so the absence of a bourse UI on a branch is not protection — a
    shell, a management command or any future code could still reach them.
    The guard sits beside the issuance guard so both asymmetries live in one
    place.
    """
    from toto.assets.issuer import is_monetary_master

    if not is_monetary_master():
        raise ValidationError(
            "Assets are traded on the master platform, not here. This "
            "platform holds a single billing currency assigned to it; open a "
            "bourse proposal on the master to exchange anything.")


def get_exchange_rate(from_asset: Asset, to_asset: Asset):
    _require_trading_host()
    if from_asset.pk == to_asset.pk:
        return None, Decimal("1"), Decimal("0"), "same_asset"

    raise ValidationError(
        f"No automatic exchange rate from {from_asset.unit_name} to {to_asset.unit_name}. "
        "Open a bourse proposal instead."
    )


def quote_exchange(*, from_asset: Asset, to_asset: Asset, amount: Decimal) -> ExchangeQuote:
    _require_trading_host()
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
    _require_trading_host()
    source_asset = get_currency_asset(currency_code)
    if not source_asset:
        raise ValidationError(f"No asset is linked to {currency_code}.")
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
