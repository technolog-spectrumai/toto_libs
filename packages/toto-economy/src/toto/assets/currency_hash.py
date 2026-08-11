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

On supply: the preimage commits the TOTAL SUPPLY, because supply is strictly
fixed. It is created once by ``create_asset()`` into the asset's reserve
account and never again — there is no mint verb and no burn verb anywhere on
the platform, and ``TransactionType`` has no such member. More of something in
circulation means releasing from the reserve; something genuinely new means a
new asset with a new identity. So the amount is safe to commit, and committing
it makes the hash a complete attestation: a holder can verify from the document
alone exactly how much of this will ever exist.

A branch allocation is a TRANSFER, not an increase. Sending a branch more
currency moves units out of Zenobia's reserve into the branch's; there is
visibly more on that branch and exactly as much in the world.

If flexible supply is ever wanted, ``v`` is the upgrade path: ``v1`` *means*
fixed supply by definition, so a ``v2`` document could carry a policy without
invalidating or re-hashing a single existing currency.
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

#: The one document version there is. v1 means fixed supply by definition —
#: see the note on flexible supply in the module docstring.
GENESIS_VERSION = 1

#: Exactly these keys, no more, no fewer. A document with an extra key is
#: malformed rather than tolerated: silently dropping it would let two
#: differing documents share a hash.
GENESIS_FIELDS = frozenset({
    "v", "issuer_fingerprint", "unit_name", "name", "decimals",
    "total_supply_base_units", "genesis_nonce", "issued_at",
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
    if document["v"] != GENESIS_VERSION:
        raise GenesisError(
            f"Unknown genesis version {document['v']!r}; this build writes and "
            f"reads v{GENESIS_VERSION}.")
    supply = document["total_supply_base_units"]
    if not isinstance(supply, int) or isinstance(supply, bool) or supply <= 0:
        raise GenesisError(
            "total_supply_base_units must be a positive integer of base units "
            "— it is the whole supply that will ever exist, and a float would "
            "make the identity bytes depend on repr().")
    decimals = document["decimals"]
    if not isinstance(decimals, int) or isinstance(decimals, bool) \
            or not 0 <= decimals <= 19:
        raise GenesisError("decimals must be an integer between 0 and 19.")
    return document


def compute_currency_hash(document: dict) -> str:
    """The permanent identity of a currency."""
    body = canonical(_validated(document))
    return HASH_PREFIX + hashlib.sha256(framed(body)).hexdigest()


def build_genesis(*, issuer_fingerprint: str, unit_name: str, name: str,
                  decimals: int, total_supply_base_units: int,
                  issued_at: str) -> dict:
    """A fresh genesis document, nonce included.

    The nonce is what makes identically-described currencies distinct: a
    re-issue after a retirement is a NEW identity, never a collision with the
    old one. ``issued_at`` arrives as a string so the caller decides the clock
    — a pure module must not read time.
    """
    return _validated({
        "v": GENESIS_VERSION,
        "issuer_fingerprint": issuer_fingerprint,
        "unit_name": unit_name,
        "name": name,
        "decimals": decimals,
        "total_supply_base_units": total_supply_base_units,
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
