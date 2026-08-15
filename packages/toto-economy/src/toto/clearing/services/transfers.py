"""Inter-platform transfers: prepare → fulfill | reject.

Sender: hold the value in escrow and announce a prepare, both in one database
transaction. Receiver: credit the destination and answer with a SIGNED fulfill
receipt — or refuse with a signed reject. Sender then posts the hold (fulfill)
or voids it (reject).

**The in-doubt rule**, encoded rather than documented: the receiver's signed
fulfill receipt is authoritative, and the sender's hold always outlives the
receiver's deadline by a fixed margin. The receiver has therefore always decided
before the sender is allowed to auto-void, so "sender voided while receiver
credited" cannot arise from timing — only from a clock catastrophe, which
surfaces as a break rather than as silent divergence.

Accounting direction (issuer-homed):
* home side (``issued_here``) sending  : user → escrow → vostro
* home side receiving                  : vostro → user
* mirror side sending                  : user → escrow → reserve (burn)
* mirror side receiving                : reserve → user (issue)
Mirror circulation therefore always equals the home vostro position, which is
what the nightly checkpoint verifies.
"""

from __future__ import annotations

import uuid as uuid_lib
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.assets.models import LedgerAccount, from_base_units
from toto.assets.services.assets import transfer_asset

from ..models import (
    ClearingHold,
    ClearingTransfer,
    LedgerPeer,
    SharedAsset,
)
from . import bridge, holds as holds_service
from . import trustline as trustline_service


def _receiver_deadline() -> "timezone.datetime":
    return timezone.now() + timedelta(seconds=holds_service._ttl_seconds())


def send_to_peer(*, peer: LedgerPeer, shared: SharedAsset,
                 origin_account: LedgerAccount, remote_account_code: str,
                 amount_base: int) -> ClearingTransfer:
    """Sender side. One transaction: hold + transfer row + signed prepare."""
    if shared.peer_id != peer.pk:
        raise ValidationError(_("That asset is not shared with this peer."))
    trustline_service.check_credit(shared, amount_base)

    transfer_uuid = uuid_lib.uuid4()
    deadline = _receiver_deadline()
    with transaction.atomic():
        hold = holds_service.create_hold(
            shared=shared, origin_account=origin_account,
            amount_base=amount_base, purpose=ClearingHold.PURPOSE_TRANSFER,
            hold_uuid=transfer_uuid,
            # The sender's hold outlives the receiver's deadline. This margin
            # IS the in-doubt rule.
            extra_ttl_seconds=holds_service.sender_margin_seconds(),
        )
        record = ClearingTransfer.objects.create(
            uuid=transfer_uuid, peer=peer, direction=ClearingTransfer.DIRECTION_OUT,
            shared_asset=shared, amount_base_units=amount_base,
            remote_account_code=remote_account_code,
            local_account=origin_account, hold=hold, deadline=deadline,
        )
        bridge.enqueue(peer, "transfer.prepare", {
            "transfer_uuid": str(transfer_uuid),
            "unit_name": shared.asset.unit_name,
            "amount_base_units": amount_base,
            "receiver_account_code": remote_account_code,
            "sender_account_code": origin_account.code,
            "deadline": deadline.isoformat(),
        })
        return record


def _refuse(peer: LedgerPeer, transfer_uuid, reason: str, status: str):
    """Refuse a prepare with a SIGNED reject the sender can verify.

    The reject is enqueued before the refusal propagates; the inbox records the
    refusal without rolling back, so the sender learns the outcome either from
    the response relay or from the durable outbox — never from a timeout alone.
    """
    bridge.enqueue(peer, "transfer.reject", {
        "transfer_uuid": str(transfer_uuid),
        "reason": reason,
        "status": status,
        "at": timezone.now().isoformat(),
    })
    raise bridge.InboxRefusal(reason, status=status)


def apply_prepare(peer: LedgerPeer, payload: dict) -> dict:
    """Receiver side: credit the destination and answer with a fulfill receipt.

    Runs inside the inbox transaction, so the credit, the transfer row and the
    outgoing receipt are one durable unit.
    """
    unit_name = payload.get("unit_name", "")
    amount_base = int(payload.get("amount_base_units", 0))
    transfer_uuid = payload.get("transfer_uuid")
    account_code = payload.get("receiver_account_code", "")

    shared = SharedAsset.objects.filter(
        peer=peer, asset__unit_name=unit_name, enabled=True).select_related(
        "asset", "asset__reserve_account", "vostro_account").first()
    if shared is None:
        # A private asset — or one this peer has no trustline for — never
        # crosses. Refusal is the whole point of the trustline model.
        _refuse(peer, transfer_uuid,
                f"no trustline for {unit_name} with this peer", "no-trustline")

    destination = LedgerAccount.objects.filter(code=account_code).first()
    if destination is None:
        _refuse(peer, transfer_uuid, f"unknown account {account_code}",
                "unknown-account")
    if amount_base <= 0:
        _refuse(peer, transfer_uuid, "amount must be positive", "bad-amount")

    # Where the credit comes from depends on which side holds the asset's home.
    source = shared.vostro_account if shared.issued_here else shared.asset.reserve_account
    if source is None:
        _refuse(peer, transfer_uuid, "trustline has no funding account",
                "misconfigured")

    amount = from_base_units(amount_base, shared.asset.decimals)
    try:
        txn = transfer_asset(
            asset=shared.asset, sender_account=source,
            receiver_account=destination, amount=amount,
            reference=f"clr:credit:{transfer_uuid}",
            description=f"Clearing credit from {peer.platform_id}",
        )
    except ValidationError as exc:
        # Insufficient vostro/reserve is a legitimate refusal (the peer sent
        # more than its position backs), not a crash.
        _refuse(peer, transfer_uuid, str(exc), "rejected")

    # Stamp the portable identity: this row came from the peer.
    type(txn).objects.filter(pk=txn.pk).update(
        origin_platform=peer.platform_id, origin_uuid=transfer_uuid)

    record = ClearingTransfer.objects.create(
        uuid=transfer_uuid, peer=peer, direction=ClearingTransfer.DIRECTION_IN,
        shared_asset=shared, amount_base_units=amount_base,
        remote_account_code=payload.get("sender_account_code", ""),
        local_account=destination, ledger_txn=txn,
        state=ClearingTransfer.FULFILLED,
        deadline=timezone.now(),
    )
    receipt = bridge.enqueue(peer, "transfer.fulfill", {
        "transfer_uuid": str(transfer_uuid),
        "credited_txn_uuid": str(txn.uuid),
        "at": timezone.now().isoformat(),
    })
    ClearingTransfer.objects.filter(pk=record.pk).update(
        receipt_signature=receipt.signature)
    return {"status": "fulfilled", "transfer_uuid": str(transfer_uuid),
            "receipt_signature": receipt.signature}


def apply_fulfill(peer: LedgerPeer, payload: dict) -> dict:
    """Sender side: the receiver credited — post the hold. Idempotent."""
    transfer_uuid = payload.get("transfer_uuid")
    record = ClearingTransfer.objects.filter(
        uuid=transfer_uuid, peer=peer,
        direction=ClearingTransfer.DIRECTION_OUT).select_related(
        "hold", "shared_asset", "shared_asset__asset").first()
    if record is None:
        raise bridge.InboxRefusal("unknown transfer", status="unknown-transfer")

    shared = record.shared_asset
    destination = (shared.vostro_account if shared.issued_here
                   else shared.asset.reserve_account)
    holds_service.post_hold(record.hold, destination=destination)
    ClearingTransfer.objects.filter(pk=record.pk).update(
        state=ClearingTransfer.FULFILLED,
        receipt_signature=payload.get("receipt_signature", ""))
    return {"status": "ok"}


def apply_reject(peer: LedgerPeer, payload: dict) -> dict:
    """Sender side: the receiver refused — void the hold. Idempotent."""
    transfer_uuid = payload.get("transfer_uuid")
    record = ClearingTransfer.objects.filter(
        uuid=transfer_uuid, peer=peer,
        direction=ClearingTransfer.DIRECTION_OUT).select_related("hold").first()
    if record is None:
        raise bridge.InboxRefusal("unknown transfer", status="unknown-transfer")
    holds_service.void_hold(record.hold)
    ClearingTransfer.objects.filter(pk=record.pk).update(
        state=ClearingTransfer.REJECTED,
        reason=str(payload.get("reason", ""))[:200])
    return {"status": "ok"}


# The inbox dispatch table. A kind with no entry is refused, so adding a
# message type is a deliberate act in two places (here and the wire contexts).
HANDLERS = {
    "transfer.prepare": apply_prepare,
    "transfer.fulfill": apply_fulfill,
    "transfer.reject": apply_reject,
}


def dispatch(peer: LedgerPeer, kind: str, payload: dict) -> dict:
    handler = HANDLERS.get(kind)
    if handler is None:
        raise bridge.InboxRefusal(f"unsupported message kind {kind}",
                                  status="unsupported")
    return handler(peer, payload)
