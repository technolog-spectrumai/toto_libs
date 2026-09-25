"""Room keys: made once, wrapped, and opened by the server for members.

The trust model is SERVER-SIDE encryption at rest (SECURITY.md says why not
end-to-end: the platform has no per-person encryption keys, no key discovery
and no browser key store; gervazy is custody, not identity). So the server
holds each encrypted room's key, and the database holds it only wrapped:

    FORUM_VAULT_PASSWORD ─Argon2id▶ UKEK ▶ VMK ▶ DEK (the `forum-rooms` box)
                                                  └─AES-GCM▶ room key ─▶ messages
    room password ─Argon2id▶ wrap key ─AES-GCM▶ room key          (password rooms)
    shared cache, TTL = expiry ─▶ room key                        (temporary rooms)

* A persistent encrypted room's key is wrapped under the platform box — the
  server can read the room for its members, and a lost *room* password never
  loses a room.
* A password room's key is ALSO wrapped under a key derived from its password,
  so a lost *platform* secret leaves it recoverable by the people who know it.
* A temporary room's key is never written to the database. It lives in the
  shared cache with a TTL equal to the room's expiry; when it goes, the
  ciphertext left behind is unreadable by anybody (crypto-shredding), and the
  expiry sweep deletes it anyway.

The key never reaches a browser. A process unwraps it on demand and keeps it
in a small in-process cache keyed by room and key version.
"""

from __future__ import annotations

import hmac
import os
import threading

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from toto.gervazy.crypto import aes_gcm_decrypt, aes_gcm_encrypt
from toto.gervazy.strongbox import Strongbox, VaultUnavailable

vault = Strongbox(name="forum-rooms", owner_username="forum-vault",
                  password_setting="FORUM_VAULT_PASSWORD", label="Forum")

KEY_SIZE = 32
SALT_SIZE = 16
DEFAULT_KDF = {"memory_cost": 65536, "iterations": 3, "lanes": 4}

_local: dict[tuple[int, int], bytes] = {}
_lock = threading.Lock()


class RoomKeyUnavailable(Exception):
    """This room's key cannot be opened here and now."""


def kdf_params() -> dict:
    return {**DEFAULT_KDF, **(getattr(settings, "FORUM_PASSWORD_KDF", None) or {})}


def derive_password_keys(password: str, salt: bytes, *, memory_cost: int,
                         iterations: int, lanes: int) -> tuple[bytes, bytes]:
    """ONE Argon2id derivation, split: (wrap key, verifier). The two halves are
    independent outputs, so storing the verifier reveals nothing about the key
    that wraps the room key. The password itself is never stored."""
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    raw = Argon2id(salt=bytes(salt), length=64, iterations=iterations, lanes=lanes,
                   memory_cost=memory_cost).derive((password or "").encode("utf-8"))
    return raw[:32], raw[32:]


def _key_aad(channel) -> bytes:
    return f"toto:forum:roomkey:v1:{channel.pk}".encode()


def _cache_key(channel) -> str:
    return f"forum:roomkey:{channel.pk}"


def shared_key_store_available() -> bool:
    """Is the cache shared across processes? A temporary room's key lives only
    there, so a per-process cache would make it unreadable from the next worker."""
    if getattr(settings, "FORUM_ALLOW_LOCAL_KEY_STORE", False):
        return True
    backend = (settings.CACHES.get("default") or {}).get("BACKEND", "")
    return not any(word in backend for word in ("locmem", "dummy"))


def set_password(channel, password: str, *, room_key: bytes | None = None) -> None:
    """Store a verifier (and, for an encrypted persistent room, re-wrap the room
    key under the new password). The caller saves the channel."""
    params = kdf_params()
    salt = os.urandom(SALT_SIZE)
    wrap, verifier = derive_password_keys(password, salt, **params)
    channel.password_salt = salt
    channel.password_verifier = verifier
    channel.kdf_memory_cost = params["memory_cost"]
    channel.kdf_iterations = params["iterations"]
    channel.kdf_lanes = params["lanes"]
    if room_key is not None and channel.pk and hasattr(channel, "room_key"):
        from .models import ForumRoomKey

        row = ForumRoomKey.objects.filter(channel=channel).first()
        if row is not None:
            ct, nonce = aes_gcm_encrypt(wrap, room_key, _key_aad(channel))
            row.password_wrapped, row.password_nonce = ct, nonce
            row.save(update_fields=["password_wrapped", "password_nonce"])


def verify_password(channel, password: str) -> bool:
    if not channel.password_verifier or not channel.password_salt:
        return False
    _wrap, verifier = derive_password_keys(
        password, bytes(channel.password_salt), memory_cost=channel.kdf_memory_cost,
        iterations=channel.kdf_iterations, lanes=channel.kdf_lanes)
    return hmac.compare_digest(verifier, bytes(channel.password_verifier))


def create_room_key(channel, *, password: str | None = None) -> bytes:
    """Make the room's key once; persist it wrapped (or cache-only if temporary)."""
    key = os.urandom(KEY_SIZE)
    if channel.is_temporary:
        seconds = int((channel.expires_at - timezone.now()).total_seconds())
        cache.set(_cache_key(channel), key, timeout=max(1, seconds))
        _remember(channel, 0, key)
        return key
    from .models import ForumRoomKey

    session = vault.open_session()
    box = vault.ensure()
    wrapped = box.data_keys.filter(state="active").order_by("id").first()
    if wrapped is None:
        wrapped = session.create_data_key()
    ct, nonce = session.encrypt_blob(wrapped, key, aad=_key_aad(channel))
    row = ForumRoomKey(channel=channel, platform_wrapped=ct, platform_nonce=nonce,
                       platform_wrapped_key=wrapped)
    if password:
        wrap, _v = derive_password_keys(
            password, bytes(channel.password_salt), memory_cost=channel.kdf_memory_cost,
            iterations=channel.kdf_iterations, lanes=channel.kdf_lanes)
        row.password_wrapped, row.password_nonce = aes_gcm_encrypt(wrap, key, _key_aad(channel))
    row.save()
    _remember(channel, row.version, key)
    return key


def _remember(channel, version, key):
    with _lock:
        _local[(channel.pk, version)] = key


def forget(channel) -> None:
    with _lock:
        for k in [k for k in _local if k[0] == channel.pk]:
            _local.pop(k, None)


def open_key(channel) -> bytes:
    """The room key, or RoomKeyUnavailable. Never plaintext fallback."""
    if not channel.is_encrypted:
        raise RoomKeyUnavailable("This room is not encrypted.")
    if channel.is_temporary:
        key = cache.get(_cache_key(channel))
        if key is None:
            raise RoomKeyUnavailable("This temporary room's key has expired.")
        return key
    from .models import ForumRoomKey

    row = ForumRoomKey.objects.filter(channel=channel).select_related(
        "platform_wrapped_key").first()
    if row is None:
        raise RoomKeyUnavailable("This room has no key.")
    with _lock:
        cached = _local.get((channel.pk, row.version))
    if cached is not None:
        return cached
    try:
        session = vault.open_session(create=False)
        key = session.decrypt_blob(row.platform_wrapped_key, bytes(row.platform_wrapped),
                                   bytes(row.platform_nonce), aad=_key_aad(channel))
    except (VaultUnavailable, Exception) as exc:  # noqa: BLE001
        raise RoomKeyUnavailable(
            "The platform cannot open this room's key"
            + ("; its members can recover it with the room password." if row.password_wrapped else ".")
        ) from exc
    _remember(channel, row.version, key)
    return key


def open_key_with_password(channel, password: str) -> bytes:
    """Recovery: the room key from its password alone (no platform secret)."""
    from .models import ForumRoomKey

    row = ForumRoomKey.objects.filter(channel=channel).first()
    if row is None or not row.password_wrapped:
        raise RoomKeyUnavailable("This room's key is not wrapped under a password.")
    wrap, _v = derive_password_keys(
        password, bytes(channel.password_salt), memory_cost=channel.kdf_memory_cost,
        iterations=channel.kdf_iterations, lanes=channel.kdf_lanes)
    try:
        return aes_gcm_decrypt(wrap, bytes(row.password_wrapped), bytes(row.password_nonce),
                               _key_aad(channel))
    except Exception as exc:  # noqa: BLE001
        raise RoomKeyUnavailable("Wrong password.") from exc


def shred(channel) -> None:
    """Destroy every copy of the key this platform holds."""
    cache.delete(_cache_key(channel))
    from .models import ForumRoomKey

    ForumRoomKey.objects.filter(channel=channel).delete()
    forget(channel)
