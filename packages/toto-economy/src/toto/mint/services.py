"""Issuing an asset: the ledger movement plus the record of who and why."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils.translation import gettext as _

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


def _post_supply_change(*, asset, kind: str, amount_base_units: int,
                        reserve_account, reference: str, description: str):
    """The double entry behind a mint or a burn, and nothing else.

    Units enter the world at ``system_issuance`` and leave it there. A mint
    moves them from that account into the reserve; a burn moves them back and
    they are gone. The ledger stays balanced in both directions, so the hash
    chain over transactions keeps meaning what it meant.
    """
    from toto.assets.hashing import attach_hash
    from toto.assets.models import (AssetHolding, LedgerEntry,
                                    LedgerTransaction, TransactionType)
    from toto.assets.services.assets import _get_system_issuance_account

    system_account = _get_system_issuance_account()
    signed = amount_base_units if kind == "mint" else -amount_base_units

    holding, _ = AssetHolding.objects.get_or_create(
        asset=asset, account=reserve_account,
        defaults={"balance_base_units": 0})
    holding.balance_base_units += signed
    holding.save(update_fields=["balance_base_units", "updated_at"])

    tx = LedgerTransaction.objects.create(
        reference=reference,
        transaction_type=(TransactionType.MINT if kind == "mint"
                          else TransactionType.BURN),
        description=description, asset=asset)
    LedgerEntry.objects.create(transaction=tx, account=system_account,
                               asset=asset, amount_base_units=-signed)
    LedgerEntry.objects.create(transaction=tx, account=reserve_account,
                               asset=asset, amount_base_units=signed)
    tx.posted = True
    tx.save(update_fields=["posted"])
    attach_hash(tx)
    return tx


def _reserve_of(asset, reserve_account=None):
    reserve = reserve_account or asset.reserve_account
    if reserve is None:
        raise ValidationError(
            f"“{asset.unit_name}” has no reserve account, so there is nowhere "
            "for minted units to go. Minted units are never loose: they land "
            "in the reserve and leave it by an ordinary transfer.")
    return reserve


def mint(*, asset, amount: Decimal | None = None,
         amount_base_units: int | None = None, reason: str, actor=None,
         reserve_account=None, reference: str = ""):
    """MINT: bring units of an engraved currency into existence.

    Master only, into the reserve, never past the engraved maximum — and the
    maximum is in the currency hash, so anyone holding the genesis document can
    check that a refusal here was right.

    Returns the chain event. The ledger posting hangs off it, because the
    question "how much of this exists" is answered by the chain and the ledger
    merely agrees.
    """
    from toto.assets.issuer import require_master
    from toto.assets.models import to_base_units

    from .history import maximum, supply

    # Authority first, before any arithmetic and long before any posting: a
    # branch has no business reaching this at all, and a refusal that comes
    # after a ledger movement is a refusal that left a mess behind.
    require_master("mint currency")
    amount_base = _amount_base(asset, amount, amount_base_units,
                               to_base_units)

    with transaction.atomic():
        reserve = _reserve_of(asset, reserve_account)
        already = supply(asset)
        ceiling = maximum(asset)
        if already + amount_base > ceiling:
            raise ValidationError(
                f"Minting {amount_base} would take {asset.unit_name} past the "
                f"maximum engraved into its identity ({already} of {ceiling} "
                f"base units already exist, so {ceiling - already} may still "
                "be minted). A maximum cannot be raised — needing more means "
                "engraving a new currency.")

        tx = _post_supply_change(
            asset=asset, kind="mint", amount_base_units=amount_base,
            reserve_account=reserve,
            reference=reference or f"mint-{asset.unit_name.lower()}-{already}",
            description=reason)
        return append_event(asset=asset, kind="mint",
                            amount_base_units=amount_base, reason=reason,
                            actor=actor, ledger_transaction=tx)


def burn(*, asset, amount: Decimal | None = None,
         amount_base_units: int | None = None, reason: str, actor=None,
         reserve_account=None, reference: str = ""):
    """BURN: destroy units, and only ones nobody holds.

    Taking exclusively from the reserve is what makes burning the exact inverse
    of minting: no user's balance can be reduced by an act they took no part
    in. Anything held outside the reserve has to come back first, by an
    ordinary transfer, and coming back is a decision somebody makes.
    """
    from toto.assets.issuer import require_master
    from toto.assets.models import to_base_units
    from toto.assets.queries import get_asset_balance

    from .history import supply

    require_master("burn currency")
    amount_base = _amount_base(asset, amount, amount_base_units,
                               to_base_units)

    with transaction.atomic():
        reserve = _reserve_of(asset, reserve_account)
        held = get_asset_balance(asset, reserve)
        if amount_base > held:
            raise ValidationError(
                f"The {asset.unit_name} reserve holds {held} base units, so "
                f"{amount_base} cannot be burned. Only units nobody holds can "
                "be destroyed; the rest have to be returned first.")

        tx = _post_supply_change(
            asset=asset, kind="burn", amount_base_units=amount_base,
            reserve_account=reserve,
            reference=(reference
                       or f"burn-{asset.unit_name.lower()}-{supply(asset)}"),
            description=reason)
        return append_event(asset=asset, kind="burn",
                            amount_base_units=amount_base, reason=reason,
                            actor=actor, ledger_transaction=tx)


def _amount_base(asset, amount, amount_base_units, to_base_units) -> int:
    """Exactly one of the two ways of saying how much, converted to base."""
    if (amount is None) == (amount_base_units is None):
        raise ValidationError(
            "Say how much either as a display amount or as base units, not "
            "both and not neither — the two scales are exactly the mix-up "
            "that decimals exist to prevent.")
    base = (int(amount_base_units) if amount_base_units is not None
            else to_base_units(amount, asset.decimals))
    if base <= 0:
        raise ValidationError(_("A monetary act moves a positive amount."))
    return base


def create_currency(*, name: str, unit_name: str, total_supply: Decimal,
                    decimals: int, reserve_account, reference: str,
                    description: str = "", metadata=None, code: str = "",
                    symbol: str = "", actor=None):
    """ENGRAVE a currency and MINT its opening supply, in one operator act.

    Two verbs, one convenience — and they stay two verbs underneath, so the
    opening supply is an ordinary event on the chain rather than a special
    case. Minting the rest later, or burning some of this, needs no new
    machinery and no new identity.

    Lives here rather than in ``toto.assets`` because it creates units, and
    creating units is the mint's business. The ledger ships to every host; the
    mint does not.
    """
    from toto.assets.services.assets import engrave_currency

    reason = (description or "").strip() or f"Opening supply of {unit_name}."

    with transaction.atomic():
        asset = engrave_currency(
            name=name, unit_name=unit_name, max_supply=total_supply,
            decimals=decimals, metadata=metadata, code=code, symbol=symbol)
        asset.reserve_account = reserve_account
        asset.save(update_fields=["reserve_account", "updated_at"])

        mint(asset=asset, amount=total_supply, reason=reason, actor=actor,
             reserve_account=reserve_account, reference=reference)
        return asset


def issue_asset(*, name: str, unit_name: str, total_supply: Decimal,
                decimals: int, reason: str, actor=None, code: str = "",
                symbol: str = "", reserve_code: str = "currency-reserve"):
    """Engrave a currency, mint its opening supply, and record the decision.

    ``total_supply`` is the maximum engraved into the identity, and it is all
    minted here — the ordinary case. Minting less and the rest later is
    ``create_currency`` plus ``mint``; this is the one-act front door the
    issuance desk uses.
    """
    from toto.assets.models import (AccountType, LedgerAccount,
                                    LedgerTransaction, TransactionType)

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

        asset = create_currency(
            name=name, unit_name=unit_name, total_supply=total_supply,
            decimals=decimals, reserve_account=reserve,
            reference=f"issue-{unit_name.lower()}-{IssuanceRecord.objects.count() + 1}",
            description=reason, code=code, symbol=symbol, actor=actor)

        creation = LedgerTransaction.objects.filter(
            asset=asset, transaction_type=TransactionType.MINT).first()

        IssuanceRecord.objects.create(
            asset=asset, currency_hash=asset.currency_hash,
            unit_name=asset.unit_name,
            max_supply_base_units=asset.max_supply_base_units,
            decimals=asset.decimals, actor=actor, reason=reason,
            genesis_payload=asset.genesis_payload,
            genesis_signature=asset.genesis_signature,
            ledger_transaction=creation)
        return asset
