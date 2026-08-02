"""The clearing wire format: canonical JSON, domain-separated Ed25519 envelopes.

Every message between the two platforms is a dict serialized canonically (sorted
keys, no whitespace variance), framed with a DOMAIN CONTEXT before signing so a
signature over one message kind can never be replayed as another — the same
discipline as the identity layer's enrollment MACs, with clearing's own context
strings (contexts are never shared across subsystems).

The signature covers ``context || 0x00 || len(body)_be32 || body`` — an explicit
length prefix, so no concatenation of fields can collide with another framing.
"""

from __future__ import annotations

import hashlib
import json
import struct

# One context per message kind, versioned. Adding a kind = adding a constant;
# reusing a constant for a new meaning is the bug this table exists to prevent.
CONTEXTS = {
    "handshake": b"toto/clearing/v1/handshake",
    "transfer.prepare": b"toto/clearing/v1/transfer.prepare",
    "transfer.fulfill": b"toto/clearing/v1/transfer.fulfill",
    "transfer.reject": b"toto/clearing/v1/transfer.reject",
    "mirror.page": b"toto/clearing/v1/mirror.page",
    "statement": b"toto/clearing/v1/statement",
    "checkpoint": b"toto/clearing/v1/checkpoint",
    "swap.propose": b"toto/clearing/v1/swap.propose",
    "swap.accept": b"toto/clearing/v1/swap.accept",
    "swap.execute": b"toto/clearing/v1/swap.execute",
    "swap.void": b"toto/clearing/v1/swap.void",
}


def canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, default=str).encode()


def framed(kind: str, body: bytes) -> bytes:
    context = CONTEXTS[kind]
    return context + b"\x00" + struct.pack(">I", len(body)) + body


def payload_hash(kind: str, payload: dict) -> str:
    return hashlib.sha256(framed(kind, canonical(payload))).hexdigest()


def sign_envelope(peer, kind: str, payload: dict) -> dict:
    """Wrap ``payload`` in a signed envelope bound to ``kind``."""
    body = canonical(payload)
    return {
        "kind": kind,
        "payload": payload,
        "payload_hash": hashlib.sha256(framed(kind, body)).hexdigest(),
        "signature": peer.sign(framed(kind, body)),
        "platform_id": _self_platform_id(),
    }


def verify_envelope(peer, envelope: dict) -> bool:
    """True iff the envelope's signature is the peer's, over this exact kind."""
    kind = envelope.get("kind", "")
    if kind not in CONTEXTS:
        return False
    body = canonical(envelope.get("payload") or {})
    return peer.verify_peer(framed(kind, body), envelope.get("signature", ""))


def _self_platform_id() -> str:
    from django.conf import settings

    return getattr(settings, "CLEARING_SELF_PLATFORM_ID", "") or getattr(
        settings, "PLATFORM_DOMAIN", "unknown")
