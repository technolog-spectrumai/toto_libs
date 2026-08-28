"""Detached Ed25519 signatures over a block.

**A signature is applied at append time, not afterwards.** `LedgerEntry.save()`
refuses an update, which is exactly right and also means there is no moment
later at which a signature could be attached. So `append()` takes a `signer`
and calls it with the block's canonical XML once the hash is known.

**Key custody is the caller's problem, deliberately.** This module takes a
private key and gives back a callable; it has no opinion about where that key
lived. The house answer is `toto.gervazy` — Ed25519 private keys encrypted into
a person's strongbox, `SigningService.verify` needing no password — and
`gervazy_signer` below adapts it. Building custody into the chain engine would
make an engine that is supposed to be reusable depend on one platform's idea of
who holds keys.

Signing is optional. An unsigned chain still verifies: the hash chain proves
the blocks agree with each other, and a signature adds *who said so*.
"""

from __future__ import annotations

import base64

from toto.ledger.services.chain import entry_block_xml


class SignatureError(RuntimeError):
    """A block could not be signed, or a signature could not be read."""


ALGORITHM = "ed25519"


def ed25519_signer(private_key, *, key_id: str = ""):
    """A signer callable over a raw Ed25519 private key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    if not isinstance(private_key, Ed25519PrivateKey):
        raise SignatureError(
            f"Expected an Ed25519 private key, got {type(private_key).__name__}."
        )

    def sign(block_text: str):
        signature = private_key.sign(block_text.encode("utf-8"))
        return base64.b64encode(signature).decode("ascii"), key_id, ALGORITHM

    return sign


def gervazy_signer(session, person, *, wrapped_key=None):
    """A signer backed by a person's strongbox key.

    Needs an open `GervazyCryptoSession`, which is what makes signing an
    explicit, password-backed act rather than something a background job can
    do on someone's behalf.
    """
    from toto.gervazy.signing import SigningService

    def sign(block_text: str):
        signed = SigningService.sign_document(
            session, person, block_text.encode("utf-8"), wrapped_key=wrapped_key,
        )
        return signed.signature_b64, signed.signing_key_id, ALGORITHM

    return sign


def verify_signature(entry, public_key_pem: str) -> bool:
    """Is this block's signature good for that public key?

    Verifies over the block's canonical XML — the same text the hash was taken
    over — so a signature covers the block's whole content and its place in the
    chain, not just an opaque digest that could be lifted onto another block.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization

    if not entry.signature:
        return False
    if entry.signature_algorithm and entry.signature_algorithm != ALGORITHM:
        raise SignatureError(
            f"This build cannot verify {entry.signature_algorithm!r} signatures."
        )
    try:
        public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
        public_key.verify(
            base64.b64decode(entry.signature),
            entry_block_xml(entry).encode("utf-8"),
        )
    except (InvalidSignature, ValueError, TypeError):
        return False
    return True
