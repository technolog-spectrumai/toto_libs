"""Currency identity: the genesis document, its hash, and its signature.

A currency is not its ticker, its name or its row id — those are labels, and
labels drift. A currency is its GENESIS HASH, computed once by the issuer over
a canonical document and never again. The full doctrine is
``portal/hierarchical_economy.md``; this module is the arithmetic.

Three deliberate boundaries:

- The framing (context prefix, NUL, big-endian length) copies the DISCIPLINE of
  ``toto.clearing.services.wire`` but not the code: assets must not depend on
  clearing, and the context string is this module's own, so a signature over a
  genesis document can never verify as a clearing message or vice versa.
- Nothing here touches ``assets/hashing.py``. That module hashes ledger
  transactions, its payload has no version field, and changing its input would
  make ``verify_hash_chain()`` return False for every existing chain, forever.
- ``canonical()`` is STRICTER than the wire's: no ``default=str``. Identity
  bytes must never depend on what Python's ``str()`` happens to do to a Decimal
  or a datetime — a non-JSON-native value is a caller bug and is refused.

On supply: the preimage commits a supply POLICY (``fixed`` | ``capped`` |
``open``), never the current amount. Minting exists (toto.mint, master-only),
so an amount in the preimage would mean every mint changes the currency's
identity. The policy is the monetary promise itself: a holder can verify from
the hash alone whether the issuer may dilute them, and a mint past a committed
cap is refusable by anyone holding the genesis document.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import struct

#: Versioned and owned by this module. Reusing a context string for a new
#: meaning is the bug the clearing wire's table documents; ours is distinct
#: from every entry in it by construction ("toto/currency/" vs "toto/clearing/").
GENESIS_CONTEXT = b"toto/currency/v1/genesis"

SUPPLY_FIXED = "fixed"
SUPPLY_CAPPED = "capped"
SUPPLY_OPEN = "open"
SUPPLY_POLICIES = (SUPPLY_FIXED, SUPPLY_CAPPED, SUPPLY_OPEN)

#: Exactly these keys, no more, no fewer. A document with an extra key is
#: malformed rather than tolerated: silently dropping it would let two
#: differing documents share a hash.
GENESIS_FIELDS = frozenset({
    "v", "issuer_fingerprint", "unit_name", "name", "decimals",
    "supply_policy", "supply_cap", "genesis_nonce", "issued_at",
})

HASH_PREFIX = "tcur1:"


class GenesisError(ValueError):
    """A genesis document that cannot mean anything."""


def canonical(document: dict) -> bytes:
    """The document's identity bytes. Refuses what JSON cannot say natively."""
    try:
        return json.dumps(document, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise GenesisError(
            f"A genesis document must be JSON-native throughout: {exc}"
        ) from exc


def framed(body: bytes) -> bytes:
    """``context || 0x00 || len(body)_be32 || body`` — the wire discipline."""
    return GENESIS_CONTEXT + b"\x00" + struct.pack(">I", len(body)) + body


def _validated(document: dict) -> dict:
    keys = set(document)
    if keys != GENESIS_FIELDS:
        extra = sorted(keys - GENESIS_FIELDS)
        missing = sorted(GENESIS_FIELDS - keys)
        parts = []
        if extra:
            parts.append(f"unexpected: {', '.join(extra)}")
        if missing:
            parts.append(f"missing: {', '.join(missing)}")
        raise GenesisError(f"Not a genesis document ({'; '.join(parts)}).")
    if document["supply_policy"] not in SUPPLY_POLICIES:
        raise GenesisError(
            f"Unknown supply policy {document['supply_policy']!r}; one of "
            f"{', '.join(SUPPLY_POLICIES)}.")
    has_cap = document["supply_cap"] is not None
    if (document["supply_policy"] == SUPPLY_CAPPED) != has_cap:
        raise GenesisError(
            "supply_cap is required exactly when supply_policy is "
            f"“{SUPPLY_CAPPED}” — a cap on an uncapped policy is a "
            "contradiction, not a default.")
    return document


def compute_currency_hash(document: dict) -> str:
    """The permanent identity of a currency."""
    body = canonical(_validated(document))
    return HASH_PREFIX + hashlib.sha256(framed(body)).hexdigest()


def build_genesis(*, issuer_fingerprint: str, unit_name: str, name: str,
                  decimals: int, supply_policy: str, issued_at: str,
                  supply_cap: int | None = None) -> dict:
    """A fresh genesis document, nonce included.

    The nonce is what makes identically-described currencies distinct: a
    re-issue after a retirement is a NEW identity, never a collision with the
    old one. ``issued_at`` arrives as a string so the caller decides the clock
    — a pure module must not read time.
    """
    return _validated({
        "v": 1,
        "issuer_fingerprint": issuer_fingerprint,
        "unit_name": unit_name,
        "name": name,
        "decimals": decimals,
        "supply_policy": supply_policy,
        "supply_cap": supply_cap,
        "genesis_nonce": secrets.token_hex(32),
        "issued_at": issued_at,
    })


# --------------------------------------------------------------------------- #
# Signature                                                                    #
# --------------------------------------------------------------------------- #

def sign_genesis(private_key, document: dict) -> str:
    """Ed25519 over the framed canonical bytes, hex-encoded.

    Anyone can compute the digest; only the issuer can sign it. This is what
    lets a branch verify a currency that arrived over an untrusted channel —
    and why the catalogue needs no authenticated transport.
    """
    body = canonical(_validated(document))
    return private_key.sign(framed(body)).hex()


def verify_genesis_document(public_key_pem: str, document: dict,
                            signature_hex: str) -> bool:
    """True iff the signature is the issuer's, over exactly this document.

    Returns False on ANY malformation — bad PEM, bad hex, wrong shape. This
    runs against catalogue entries pulled from a URL an operator typed, where
    a refused entry is the correct outcome and a 500 is not.
    """
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey)
        from cryptography.hazmat.primitives.serialization import (
            load_pem_public_key)

        public_key = load_pem_public_key(public_key_pem.encode())
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        body = canonical(_validated(document))
        public_key.verify(bytes.fromhex(signature_hex), framed(body))
        return True
    except Exception:  # noqa: BLE001 - refused is the outcome, never a 500
        return False
