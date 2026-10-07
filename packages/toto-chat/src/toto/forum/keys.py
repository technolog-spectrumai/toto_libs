"""Channel keys: made once, kept wrapped, opened by the server for members.

Server-side encryption at rest (SECURITY.md says why not end-to-end). Each
channel has ONE 32-byte key; the database holds it only wrapped:

    FORUM_VAULT_PASSWORD ─Argon2id▶ UKEK ▶ VMK ▶ DEK (the `forum-channels` box)
                                                  └─AES-GCM▶ channel key ─▶ content

``FORUM_VAULT_PASSWORD`` is a platform secret: ``deploy.py`` mints it once
and carries it forward on every redeploy, and it lives only in the server's
git-ignored env file. There is no channel password and no key derived from
one. Without the secret, or with another one, ``open_key`` raises
``ChannelKeyUnavailable`` and every door answers 503: nothing is ever read
or stored in clear instead. **Losing the secret loses every channel's
content for good**; a backup that holds the database and not the env file
cannot be read.

The key never reaches a browser. A process unwraps it on demand and keeps it
in a small in-process cache keyed by channel and key version, so a restart
reads the wrapped row again with the secret, which is the whole of how a key
survives one.
"""

from __future__ import annotations

import os
import threading

from django.db import IntegrityError, transaction

from toto.gervazy.strongbox import Strongbox, VaultUnavailable

vault = Strongbox(name="forum-channels", owner_username="forum-vault",
                  password_setting="FORUM_VAULT_PASSWORD", label="Forum")

KEY_SIZE = 32

_local: dict[tuple[int, int], bytes] = {}
_lock = threading.Lock()


class ChannelKeyUnavailable(Exception):
    """This channel's key cannot be opened here and now."""


def _key_aad(channel) -> bytes:
    return f"toto:forum:channelkey:v1:{channel.pk}".encode()


def _remember(channel, version, key):
    with _lock:
        _local[(channel.pk, version)] = key


def forget(channel=None) -> None:
    """Drop the in-process copies: one channel's, or all (a "restart")."""
    with _lock:
        if channel is None:
            _local.clear()
            return
        for k in [k for k in _local if k[0] == channel.pk]:
            _local.pop(k, None)


def ensure_key(channel) -> None:
    """Make the channel's key if it has none. Idempotent; a lost race is the
    winner's key. Raises ``ChannelKeyUnavailable`` when the box cannot be
    opened (no ``FORUM_VAULT_PASSWORD``), and makes nothing."""
    from .models import ForumChannelKey

    if ForumChannelKey.objects.filter(channel=channel).exists():
        return
    try:
        session = vault.open_session()
        box = vault.ensure()
        wrapped = box.data_keys.filter(state="active").order_by("id").first()
        if wrapped is None:
            wrapped = session.create_data_key()
        key = os.urandom(KEY_SIZE)
        ciphertext, nonce = session.encrypt_blob(wrapped, key, aad=_key_aad(channel))
    except VaultUnavailable as exc:
        raise ChannelKeyUnavailable(str(exc)) from exc
    try:
        with transaction.atomic():
            row = ForumChannelKey.objects.create(
                channel=channel, platform_wrapped=ciphertext, platform_nonce=nonce,
                platform_wrapped_key=wrapped)
    except IntegrityError:
        return                      # another request made it first: theirs stands
    _remember(channel, row.version, key)


def open_key(channel) -> bytes:
    """The channel key, or ``ChannelKeyUnavailable``. Never a fallback."""
    from .models import ForumChannelKey

    row = (ForumChannelKey.objects.filter(channel=channel)
           .select_related("platform_wrapped_key").first())
    if row is None:
        raise ChannelKeyUnavailable("This channel has no key.")
    with _lock:
        cached = _local.get((channel.pk, row.version))
    if cached is not None:
        return cached
    try:
        session = vault.open_session(create=False)
        key = session.decrypt_blob(row.platform_wrapped_key, bytes(row.platform_wrapped),
                                   bytes(row.platform_nonce), aad=_key_aad(channel))
    except Exception as exc:  # noqa: BLE001 - a missing secret and a wrong one look alike
        raise ChannelKeyUnavailable("The platform cannot open this channel's key.") from exc
    _remember(channel, row.version, key)
    return key
