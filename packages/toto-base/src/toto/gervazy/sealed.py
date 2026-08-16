"""Sealed content the platform and the desktop both read.

ONE construction, chosen on purpose: ``Argon2id(password, salt, costs)`` →
**XChaCha20-Poly1305**, framed as ``MAGIC(8) || VERSION(1) || nonce(24) ||
ciphertext+tag``. The desktop app implements exactly this in Rust
(``gervasius::gervazy``); frames written by either side open in the other, and
the interop is asserted from both directions rather than assumed.

Why XChaCha20 rather than the AES-256-GCM this package already uses for its own
key hierarchy: the nonce. 192 bits means a random nonce per seal is safe for as
many messages as anyone will ever write, with no counter to keep and no state
to corrupt. AES-GCM's 96-bit nonce is fine right up until something reuses one,
and then it is catastrophic. ``crypto.py``'s envelope keeps AES-GCM because it
is not this: it seals key material it generates and tracks, not user files that
travel between machines.

Why not Fernet, which ``toto.vault.strategy`` wrote until 8/2026: it is
AES-**128**-CBC + HMAC (Fernet halves the 32-byte key), it has no AAD — so a
token could be moved from one file to another undetected — and every token
carries a cleartext timestamp. ``open_any`` still reads it, because files
sealed that way exist. Nothing writes it any more.

The salt and Argon2 costs come from the user's ``UserStrongbox`` row, which is
what lets the same password derive the same key on every device.
"""
from __future__ import annotations

import base64

from nacl import bindings as _sodium

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

#: A file has no `is_encrypted` column when it is sitting on somebody's laptop,
#: so the bytes have to say what they are.
MAGIC = b"TOTOSEAL"
VERSION = 1
_HEADER_LEN = len(MAGIC) + 1
NONCE_LEN = _sodium.crypto_aead_xchacha20poly1305_ietf_NPUBBYTES
TAG_LEN = _sodium.crypto_aead_xchacha20poly1305_ietf_ABYTES

#: What the vault binds a file's ciphertext to. Not stored in the frame — the
#: opener must supply the same bytes, which is the whole point of AAD.
VAULT_AAD = b"toto:vault:file:v1"

#: Fernet's version byte 0x80, base64url-rendered. The legacy detector.
FERNET_PREFIX = b"gAAAAA"


class SealError(Exception):
    """Wrong password, tampering, or a frame we cannot parse.

    Deliberately one exception: distinguishing "wrong password" from "corrupt"
    tells an attacker which of the two they achieved.
    """


def derive_raw_key(password: str, salt: bytes, *, memory_cost: int,
                   iterations: int, lanes: int) -> bytes:
    """The 32 raw key bytes, from the strongbox's parameters."""
    if not password:
        raise SealError("A password is required.")
    return Argon2id(salt=bytes(salt), length=32, iterations=iterations,
                    lanes=lanes, memory_cost=memory_cost).derive(
                        password.encode("utf-8"))


def is_current(data: bytes) -> bool:
    """Whether these bytes are the current frame."""
    return (len(data) > _HEADER_LEN and data.startswith(MAGIC)
            and data[len(MAGIC)] == VERSION)


def is_sealed(data: bytes) -> bool:
    """Whether these bytes are sealed at all — either era."""
    return is_current(data) or data.startswith(FERNET_PREFIX)


def seal(password: str, salt: bytes, data: bytes, *, memory_cost: int,
         iterations: int, lanes: int, aad: bytes = VAULT_AAD) -> bytes:
    """Seal `data` into the shared frame."""
    key = derive_raw_key(password, salt, memory_cost=memory_cost,
                         iterations=iterations, lanes=lanes)
    nonce = _sodium.randombytes(NONCE_LEN)
    body = _sodium.crypto_aead_xchacha20poly1305_ietf_encrypt(
        data, aad, nonce, key)
    return MAGIC + bytes([VERSION]) + nonce + body


def open_frame(password: str, salt: bytes, frame: bytes, *, memory_cost: int,
               iterations: int, lanes: int, aad: bytes = VAULT_AAD) -> bytes:
    """Open a current frame."""
    if not is_current(frame) or len(frame) < _HEADER_LEN + NONCE_LEN + TAG_LEN:
        raise SealError("Not a sealed file.")
    key = derive_raw_key(password, salt, memory_cost=memory_cost,
                         iterations=iterations, lanes=lanes)
    nonce = frame[_HEADER_LEN:_HEADER_LEN + NONCE_LEN]
    body = frame[_HEADER_LEN + NONCE_LEN:]
    try:
        return _sodium.crypto_aead_xchacha20poly1305_ietf_decrypt(
            body, aad, nonce, key)
    except Exception as exc:  # noqa: BLE001 — one answer for every failure
        raise SealError("Wrong password or corrupt file.") from exc


def open_any(password: str, salt: bytes, data: bytes, *, memory_cost: int,
             iterations: int, lanes: int, aad: bytes = VAULT_AAD) -> bytes:
    """Open whatever it is — current frame or legacy Fernet token.

    One door, because every caller wants the same thing (the plaintext) and
    which era a file comes from is not their business. A legacy token carries
    no AAD, so `aad` is ignored on that path; that is a property of the old
    format, not a choice made here.
    """
    if is_current(data):
        return open_frame(password, salt, data, memory_cost=memory_cost,
                          iterations=iterations, lanes=lanes, aad=aad)
    key = derive_raw_key(password, salt, memory_cost=memory_cost,
                         iterations=iterations, lanes=lanes)
    try:
        return Fernet(base64.urlsafe_b64encode(key)).decrypt(bytes(data))
    except (InvalidToken, ValueError) as exc:
        raise SealError("Wrong password or corrupt file.") from exc
