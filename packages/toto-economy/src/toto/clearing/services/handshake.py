"""The clearing handshake: exchange platform ids, epochs and signing keys.

Runs over the already-provisioned link credentials (HTTP Basic). Each side pins
the other's Ed25519 public key on first contact; a later handshake presenting a
DIFFERENT key or epoch does not overwrite silently — it suspends the peer until
an operator re-adopts, because both are exactly what a reset or an impostor
looks like.
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from ..models import LedgerPeer
from . import wire


def build_hello(peer: LedgerPeer) -> dict:
    peer.ensure_keypair()
    peer.save(update_fields=["our_private_key_encrypted", "our_public_key_pem",
                             "updated_at"])
    return wire.sign_envelope(peer, "handshake", {
        "platform_id": wire._self_platform_id(),
        "epoch": str(peer.epoch),
        "public_key_pem": peer.our_public_key_pem,
        "sent_at": timezone.now().isoformat(),
    })


def apply_hello(peer: LedgerPeer, envelope: dict) -> dict:
    """Apply a peer's hello. Returns {"status": ...}; never overwrites a pin."""
    payload = envelope.get("payload") or {}
    presented_key = payload.get("public_key_pem", "")
    presented_epoch = payload.get("epoch", "")
    presented_id = payload.get("platform_id", "")

    with transaction.atomic():
        locked = LedgerPeer.objects.select_for_update().get(pk=peer.pk)

        if not locked.peer_public_key_pem:
            # First contact: pin. The envelope cannot be verified yet (we have
            # no key), which is why handshakes only run over the authenticated
            # transport and why the pin is trust-on-first-use for the SAME
            # operator — the signed log makes everything after auditable.
            locked.peer_public_key_pem = presented_key
            locked.peer_epoch = presented_epoch
            if presented_id and locked.platform_id != presented_id:
                locked.platform_id = presented_id
            locked.status = LedgerPeer.STATUS_ACTIVE
            locked.save(update_fields=["peer_public_key_pem", "peer_epoch",
                                       "platform_id", "status", "updated_at"])
            return {"status": "pinned"}

        # Pinned already: the envelope must verify against the PINNED key.
        if not wire.verify_envelope(locked, envelope):
            return {"status": "bad-signature"}

        if presented_key != locked.peer_public_key_pem or (
                locked.peer_epoch and presented_epoch != locked.peer_epoch):
            # A reset (or an impostor). Suspend — never silently re-pin.
            locked.status = LedgerPeer.STATUS_SUSPENDED
            locked.save(update_fields=["status", "updated_at"])
            return {"status": "suspended-epoch-or-key-change"}

        locked.status = LedgerPeer.STATUS_ACTIVE
        locked.save(update_fields=["status", "updated_at"])
        return {"status": "ok"}
