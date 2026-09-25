"""Sealing a message or an attachment under its room's key.

AES-256-GCM (`cryptography`, through gervazy's `aes_gcm_encrypt`), a fresh
96-bit random nonce per seal, and the associated data binding the ciphertext
to the room and the message it belongs to — a sealed body copied onto another
message, or into another room, fails authentication instead of decrypting.

    frame = b"\x01" || nonce(12) || ciphertext+tag

The leading byte is the format version, so a later format can coexist in the
same column. Nothing here invents a primitive; the nonce bound for random
96-bit nonces (2^32 seals per key) is orders of magnitude beyond a room.
"""

from __future__ import annotations

from toto.gervazy.crypto import aes_gcm_decrypt, aes_gcm_encrypt

VERSION = b"\x01"
NONCE_SIZE = 12


class SealBroken(Exception):
    """A frame that is not ours, or whose tag does not verify."""


def _aad(kind: str, channel_id, message_id) -> bytes:
    return f"toto:forum:{kind}:v1:{channel_id}:{message_id}".encode()


def seal_bytes(key: bytes, data: bytes, *, kind: str, channel_id, message_id) -> bytes:
    ciphertext, nonce = aes_gcm_encrypt(key, data, _aad(kind, channel_id, message_id))
    return VERSION + nonce + ciphertext


def open_bytes(key: bytes, frame: bytes, *, kind: str, channel_id, message_id) -> bytes:
    frame = bytes(frame or b"")
    if len(frame) < 1 + NONCE_SIZE + 16 or frame[:1] != VERSION:
        raise SealBroken("Not a sealed forum frame.")
    try:
        return aes_gcm_decrypt(key, frame[1 + NONCE_SIZE:], frame[1:1 + NONCE_SIZE],
                               _aad(kind, channel_id, message_id))
    except Exception as exc:  # InvalidTag, and anything a malformed frame raises
        raise SealBroken("The sealed frame did not verify.") from exc


def seal_text(key, text: str, *, channel_id, message_id) -> bytes:
    return seal_bytes(key, (text or "").encode("utf-8"), kind="msg",
                      channel_id=channel_id, message_id=message_id)


def open_text(key, frame, *, channel_id, message_id) -> str:
    return open_bytes(key, frame, kind="msg", channel_id=channel_id,
                      message_id=message_id).decode("utf-8")
