"""The bilateral bridge: sequencing, the signed outbox, and the inbox shield.

Every outbound message is signed, numbered and durable BEFORE anything tries to
send it; every inbound message is verified against the pinned peer key,
sequence-checked, and applied exactly once. Nothing here moves value — the
transfer/swap modules compose these primitives with the ledger.
"""

from __future__ import annotations

import hashlib

from django.db import transaction

from ..models import ClearingInbox, ClearingOutbox, LedgerPeer
from . import wire


def enqueue(peer: LedgerPeer, kind: str, payload: dict) -> ClearingOutbox:
    """Sign, number and persist a message. MUST be called inside the same
    atomic block as the ledger movement it announces."""
    locked = LedgerPeer.objects.select_for_update().get(pk=peer.pk)
    seq = locked.send_seq + 1
    envelope = wire.sign_envelope(locked, kind, payload)
    row = ClearingOutbox.objects.create(
        peer=locked, seq=seq, kind=kind, payload=payload,
        payload_hash=envelope["payload_hash"], signature=envelope["signature"],
    )
    LedgerPeer.objects.filter(pk=locked.pk).update(send_seq=seq)
    return row


def param_hash(payload: dict) -> str:
    return hashlib.sha256(wire.canonical(payload)).hexdigest()


class InboxRefusal(Exception):
    """The message cannot be accepted at all (signature, order, or conflict)."""

    def __init__(self, reason: str, status: str = "refused"):
        super().__init__(reason)
        self.reason = reason
        self.status = status


def receive(peer: LedgerPeer, envelope: dict, apply_fn) -> dict:
    """Verify, dedupe and apply one inbound message.

    ``apply_fn(peer, kind, payload)`` performs the domain effect and returns the
    response dict; it runs INSIDE the same transaction as the inbox row, so a
    failure leaves neither. Returns the response — the stored one on a replay.
    """
    kind = envelope.get("kind", "")
    payload = envelope.get("payload") or {}
    msg_uuid = envelope.get("uuid")
    seq = envelope.get("seq")

    if not wire.verify_envelope(peer, envelope):
        raise InboxRefusal("signature does not verify against the pinned key",
                           status="bad-signature")
    if msg_uuid is None or seq is None:
        raise InboxRefusal("message is missing uuid or seq")

    fingerprint = param_hash(payload)

    # Replay shield first: a duplicate returns the stored response verbatim,
    # and the SAME uuid carrying different parameters is a hard error rather
    # than a silent second application.
    existing = ClearingInbox.objects.filter(peer=peer, uuid=msg_uuid).first()
    if existing is not None:
        if existing.param_hash != fingerprint:
            raise InboxRefusal(
                "this message id was already used with different parameters",
                status="idempotency-conflict")
        return existing.response

    with transaction.atomic():
        locked = LedgerPeer.objects.select_for_update().get(pk=peer.pk)
        # Gap detection: the stream is ordered, so anything beyond the next
        # expected number means we missed something and must not apply out of
        # order. (Equal-or-lower is a replay whose row we would have found.)
        if seq > locked.recv_seq + 1:
            raise InboxRefusal(
                f"sequence gap: expected {locked.recv_seq + 1}, got {seq}",
                status="gap")

        row = ClearingInbox.objects.create(
            peer=locked, uuid=msg_uuid, seq=seq, kind=kind, payload=payload,
            param_hash=fingerprint,
        )
        try:
            response = apply_fn(locked, kind, payload)
            state = ClearingInbox.APPLIED
        except InboxRefusal as refusal:
            # A refusal is a decision, not a crash: record it so the sender's
            # retry gets the same answer instead of a second attempt.
            response = {"status": refusal.status, "reason": refusal.reason}
            state = ClearingInbox.REJECTED

        ClearingInbox.objects.filter(pk=row.pk).update(state=state,
                                                       response=response)
        if seq > locked.recv_seq:
            LedgerPeer.objects.filter(pk=locked.pk).update(recv_seq=seq)
        return response
