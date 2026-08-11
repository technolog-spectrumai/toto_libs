"""Issuing an asset: the ledger movement plus the record of who and why."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

#: How many times a losing appender re-reads the head and tries again. Minting
#: is rare and operator-driven, so a genuine collision is already unlikely and
#: a third one is a symptom rather than contention.
APPEND_ATTEMPTS = 5


def append_event(*, asset, kind: str, amount_base_units: int, reason: str,
                 actor=None, ledger_transaction=None):
    """Write one monetary event onto the end of the chain. Master only.

    This is the only writer of ``CurrencyMintEvent``. It reads the head, builds
    the payload naming that head, signs it and appends — so an event's position
    in the history is decided here and cannot be supplied by a caller.

    Callers are ``mint()`` and ``burn()``; nothing else has any business
    creating or destroying units. The ledger posting is passed in rather than
    made here, because the two records answer different questions and a mint
    that failed to post must not leave an event claiming it did.
    """
    from django.utils import timezone

    from toto.assets.issuer import local_issuer, require_master

    from .chain import (GENESIS_PREV, KINDS, build_event, compute_event_hash,
                        sign_event)
    from .history import chain_head
    from .models import CurrencyMintEvent

    require_master(f"{kind} currency")
    if kind not in KINDS:
        raise ValidationError(
            f"“{kind}” is not a monetary verb; there are exactly two.")
    if not asset.currency_hash:
        raise ValidationError(
            f"“{asset.unit_name}” has no genesis hash, so there is nothing to "
            "mint or burn units of.")
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError(
            "A monetary event needs a reason — it is the only record of why "
            "the supply changed, and it cannot be added afterwards.")

    issuer = local_issuer()
    private_key = issuer.private_key()

    for attempt in range(APPEND_ATTEMPTS):
        try:
            with transaction.atomic():
                head = chain_head(lock=True)
                prev = head.event_hash if head is not None else GENESIS_PREV
                sequence = (head.sequence + 1) if head is not None else 0
                payload = build_event(
                    issuer_fingerprint=issuer.fingerprint,
                    sequence=sequence,
                    kind=kind,
                    currency_hash=asset.currency_hash,
                    amount_base_units=amount_base_units,
                    prev_hash=prev,
                    issued_at=timezone.now().isoformat())

                return CurrencyMintEvent.objects.create(
                    sequence=sequence, kind=kind, asset=asset,
                    currency_hash=asset.currency_hash,
                    amount_base_units=amount_base_units,
                    prev_hash=prev,
                    event_hash=compute_event_hash(payload),
                    signature=sign_event(private_key, payload),
                    issuer_fingerprint=issuer.fingerprint,
                    payload=payload, actor=actor, reason=reason,
                    ledger_transaction=ledger_transaction)
        except IntegrityError:
            # Someone else appended between our read of the head and our
            # insert, so the database refused a second child of that head.
            # That refusal is the whole guarantee working: re-read and go
            # after the winner rather than beside it.
            if attempt == APPEND_ATTEMPTS - 1:
                raise

    raise ValidationError(  # pragma: no cover - the loop returns or raises
        "Could not append to the monetary chain; it is being written to "
        "faster than this can read it.")


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
