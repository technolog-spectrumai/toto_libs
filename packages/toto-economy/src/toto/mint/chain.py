"""The monetary event chain: the arithmetic of MINT and BURN.

``toto.assets.currency_hash`` answers "what currency is this?". This module
answers "how much of it was ever brought into existence, and by whom?" — and
the two are deliberately different questions with different answers.

The identity is engraved once and never moves. The supply moves, so it does not
live in the identity at all; it lives in an append-only chain of signed events,
one per act of creation or destruction, and supply is the sum:

    supply(currency) = Σ mint amounts − Σ burn amounts

Three deliberate boundaries, the same discipline as the genesis module:

- The context string is this module's own. A signature over a mint event can
  never verify as a genesis document or a clearing message, and vice versa —
  which is the whole reason each subsystem owns its context rather than sharing
  a generic one.
- ``canonical()`` is imported, not re-implemented. Two canonicalisations that
  drift apart would be two definitions of "the same document", and there must
  only ever be one.
- Nothing here touches ``assets/hashing.py``. That chains ledger transactions
  and its payload has no version field; this chains monetary events. They run
  alongside each other and neither can invalidate the other.

**One chain, not one per currency.** MINT and BURN are the same kind of act
against the same authority, so they share a single global order. Per-currency
supply is a filtered sum, which costs nothing, and a single chain gives the
property that matters: there is exactly one head, so there is exactly one
answer to "what has this platform done monetarily, and in what order".
"""

from __future__ import annotations

import hashlib
import struct

from toto.assets.currency_hash import GenesisError, canonical

#: Owned by this module and distinct from every genesis and clearing context.
MINT_CONTEXT = b"toto/currency/v1/mint"

#: The one event version there is.
EVENT_VERSION = 1

#: Exactly these keys. An extra one is malformed rather than ignored: silently
#: dropping it would let two differing events share a hash.
EVENT_FIELDS = frozenset({
    "v", "issuer_fingerprint", "sequence", "kind", "currency_hash",
    "amount_base_units", "prev_hash", "issued_at",
})

HASH_PREFIX = "tmev1:"

MINT = "mint"
BURN = "burn"
KINDS = (MINT, BURN)

#: The predecessor of the very first event. Not None and not absent — a fixed
#: string, so the root is an ordinary link like every other and the database
#: can enforce "one parent, one child" over the whole chain including it.
GENESIS_PREV = ""


class MintEventError(ValueError):
    """A monetary event that cannot mean anything."""


def framed(body: bytes) -> bytes:
    """``context || 0x00 || len(body)_be32 || body`` — the wire discipline."""
    return MINT_CONTEXT + b"\x00" + struct.pack(">I", len(body)) + body


def _canonical(payload: dict) -> bytes:
    try:
        return canonical(payload)
    except GenesisError as exc:
        raise MintEventError(
            f"A monetary event must be JSON-native throughout: {exc}") from exc


def _validated(payload: dict) -> dict:
    keys = set(payload)
    if keys != EVENT_FIELDS:
        extra = sorted(keys - EVENT_FIELDS)
        missing = sorted(EVENT_FIELDS - keys)
        parts = []
        if extra:
            parts.append(f"unexpected: {', '.join(extra)}")
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        raise MintEventError(f"Not a monetary event ({'; '.join(parts)}).")
    if payload["v"] != EVENT_VERSION:
        raise MintEventError(
            f"Unknown event version {payload['v']!r}; this build writes and "
            f"reads v{EVENT_VERSION}.")
    if payload["kind"] not in KINDS:
        raise MintEventError(
            f"“{payload['kind']}” is not a monetary verb. There are exactly "
            f"two that move supply: {' and '.join(KINDS)}.")
    amount = payload["amount_base_units"]
    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        raise MintEventError(
            "amount_base_units must be a positive integer of base units. A "
            "burn is a positive amount with kind=burn, never a negative mint "
            "— one verb per act, so the history reads as what happened.")
    sequence = payload["sequence"]
    if not isinstance(sequence, int) or isinstance(sequence, bool) \
            or sequence < 0:
        raise MintEventError("sequence must be a non-negative integer.")
    if not payload["currency_hash"].startswith("tcur1:"):
        raise MintEventError(
            "A monetary event names its currency by genesis hash. Anything "
            "else is a label, and labels drift.")
    return payload


def build_event(*, issuer_fingerprint: str, sequence: int, kind: str,
                currency_hash: str, amount_base_units: int, prev_hash: str,
                issued_at: str) -> dict:
    """The signable payload for one act of creation or destruction.

    ``issued_at`` arrives as a string so the caller decides the clock — a pure
    module must not read time.
    """
    return _validated({
        "v": EVENT_VERSION,
        "issuer_fingerprint": issuer_fingerprint,
        "sequence": sequence,
        "kind": kind,
        "currency_hash": currency_hash,
        "amount_base_units": amount_base_units,
        "prev_hash": prev_hash,
        "issued_at": issued_at,
    })


def compute_event_hash(payload: dict) -> str:
    """This event's permanent name, and the next event's ``prev_hash``."""
    return HASH_PREFIX + hashlib.sha256(
        framed(_canonical(_validated(payload)))).hexdigest()


def sign_event(private_key, payload: dict) -> str:
    """Ed25519 over the framed canonical bytes, hex-encoded."""
    return private_key.sign(framed(_canonical(_validated(payload)))).hex()


def verify_event(public_key_pem: str, payload: dict,
                 signature_hex: str) -> bool:
    """True iff the signature is the issuer's, over exactly this event.

    Returns False on ANY malformation. Verification runs over rows that a
    corrupted database or a bad restore could have put there, where "refused"
    is the correct outcome and a 500 is not.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key)

        public_key = load_pem_public_key(public_key_pem.encode())
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        public_key.verify(bytes.fromhex(signature_hex),
                          framed(_canonical(_validated(payload))))
        return True
    except Exception:  # noqa: BLE001 - refused is the outcome, never a 500
        return False
