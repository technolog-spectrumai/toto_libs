"""Two-phase holds: pending → posted | voided | expired.

Every transition is one ``transfer_asset`` call, so held value is ordinary,
hash-chained ledger history and the escrow account isolates it from the owner's
spendable balance by construction. The state column is advanced by
compare-and-swap (``filter(state=...).update(...)``) INSIDE the same database
transaction as the transfer, so a replayed decision finds the CAS already spent
and returns the recorded outcome instead of moving value twice.
"""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from toto.assets.models import LedgerAccount, from_base_units
from toto.assets.services.assets import transfer_asset

from ..models import ClearingHold, SharedAsset
from . import trustline as trustline_service


def _ttl_seconds() -> int:
    return int(getattr(settings, "CLEARING_HOLD_TTL_SECONDS", 900))


def sender_margin_seconds() -> int:
    """How much LONGER a sender-side hold lives than the receiver's deadline.

    The in-doubt rule depends on this being structural: the receiver has always
    decided (fulfill or reject) before the sender is allowed to auto-void.
    """
    return int(getattr(settings, "CLEARING_HOLD_SENDER_MARGIN_SECONDS", 900))


def create_hold(*, shared: SharedAsset, origin_account: LedgerAccount,
                amount_base: int, purpose: str,
                hold_uuid=None, extra_ttl_seconds: int = 0) -> ClearingHold:
    """Park value in escrow. ``hold_uuid`` may be supplied by the caller (a
    replayed remote message carries the same uuid), making creation idempotent
    end to end: the transfer reference and the hold row both key on it."""
    import uuid as uuid_lib

    if amount_base <= 0:
        raise ValidationError("Hold amount must be positive.")

    hold_uuid = hold_uuid or uuid_lib.uuid4()
    existing = ClearingHold.objects.filter(uuid=hold_uuid).first()
    if existing is not None:
        return existing

    trustline_service.check_credit(shared, amount_base)
    amount = from_base_units(amount_base, shared.asset.decimals)
    with transaction.atomic():
        txn = transfer_asset(
            asset=shared.asset,
            sender_account=origin_account,
            receiver_account=shared.escrow_account,
            amount=amount,
            reference=f"clr:hold:{hold_uuid}",
            description=f"Clearing {purpose} hold",
        )
        hold, _ = ClearingHold.objects.get_or_create(
            uuid=hold_uuid,
            defaults=dict(
                purpose=purpose,
                shared_asset=shared,
                origin_account=origin_account,
                amount_base_units=amount_base,
                expires_at=timezone.now() + timedelta(
                    seconds=_ttl_seconds() + extra_ttl_seconds),
                hold_txn=txn,
            ),
        )
        return hold


def post_hold(hold: ClearingHold, *, destination: LedgerAccount) -> ClearingHold:
    """Escrow → destination. Idempotent: a second post returns the first."""
    amount = from_base_units(hold.amount_base_units, hold.shared_asset.asset.decimals)
    with transaction.atomic():
        claimed = ClearingHold.objects.filter(
            pk=hold.pk, state=ClearingHold.PENDING).update(state=ClearingHold.POSTED)
        if not claimed:
            hold.refresh_from_db()
            if hold.state == ClearingHold.POSTED:
                return hold
            raise ValidationError(f"Hold is {hold.state}, cannot post.")
        settle = transfer_asset(
            asset=hold.shared_asset.asset,
            sender_account=hold.shared_asset.escrow_account,
            receiver_account=destination,
            amount=amount,
            reference=f"clr:post:{hold.uuid}",
            description=f"Clearing {hold.purpose} settle",
        )
        ClearingHold.objects.filter(pk=hold.pk).update(settle_txn=settle)
        hold.refresh_from_db()
        return hold


def void_hold(hold: ClearingHold, *, expired: bool = False) -> ClearingHold:
    """Escrow → origin. Idempotent the same way post is."""
    target_state = ClearingHold.EXPIRED if expired else ClearingHold.VOIDED
    amount = from_base_units(hold.amount_base_units, hold.shared_asset.asset.decimals)
    with transaction.atomic():
        claimed = ClearingHold.objects.filter(
            pk=hold.pk, state=ClearingHold.PENDING).update(state=target_state)
        if not claimed:
            hold.refresh_from_db()
            if hold.state in (ClearingHold.VOIDED, ClearingHold.EXPIRED):
                return hold
            raise ValidationError(f"Hold is {hold.state}, cannot void.")
        settle = transfer_asset(
            asset=hold.shared_asset.asset,
            sender_account=hold.shared_asset.escrow_account,
            receiver_account=hold.origin_account,
            amount=amount,
            reference=f"clr:void:{hold.uuid}",
            description=f"Clearing {hold.purpose} refund",
        )
        ClearingHold.objects.filter(pk=hold.pk).update(settle_txn=settle)
        hold.refresh_from_db()
        return hold


def expire_stale_holds() -> int:
    """The sweeper: void every pending hold past its deadline. Returns count."""
    expired = 0
    stale = ClearingHold.objects.filter(
        state=ClearingHold.PENDING, expires_at__lt=timezone.now())
    for hold in stale.select_related("shared_asset__asset",
                                     "shared_asset__escrow_account",
                                     "origin_account"):
        void_hold(hold, expired=True)
        expired += 1
    return expired
