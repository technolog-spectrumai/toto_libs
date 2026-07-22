"""Telegraph at-rest encryption — the "Discord history" channel key.

Regular relay ("Forum") messages are persisted **encrypted at rest** under a
per-channel gervazy DEK. All channel DEKs live inside one *telegraph system
strongbox* unlocked server-side by ``TELEGRAPH_VAULT_PASSWORD`` (mirroring
``sso_master``'s ``SSO_VAULT_PASSWORD`` pattern — no human in the loop).

Because the server holds the key, it can decrypt to serve **readable history** to
any member, including brand-new joiners (the Discord model). In-transit
confidentiality is TLS. This is deliberately NOT end-to-end — that trade is what
makes durable, readable history possible.

End-to-end *secure-on-send* messages never pass through the at-rest layer: they are
encrypted on the client under a member-held key and stored as opaque ``e2e`` rows.

Key hierarchy (see crypto.md):

    TELEGRAPH_VAULT_PASSWORD ─Argon2id▶ UKEK ─unwrap▶ VMK ─unwrap▶ channel DEK ─AES-GCM▶ message
"""
import base64
import json
import logging
import threading
from pathlib import Path

from django.conf import settings
from django.utils import timezone

from toto.gervazy.crypto import GervazyCryptoSession

log = logging.getLogger(__name__)

# Name of the single service strongbox that owns every channel DEK.
SYSTEM_STRONGBOX_NAME = "telegraph-system"
# Username of the service account that owns the strongbox.
SYSTEM_OWNER_USERNAME = "telegraph-vault"


class VaultUnavailable(RuntimeError):
    """The telegraph vault is not usable (no password configured, or not initialized).

    Callers degrade gracefully: messages still send live, but are not persisted and no
    history is served.
    """


# ── server-side unlock (mirrors sso_master) ────────────────────────────────────

def load_vault_password() -> str:
    password = (getattr(settings, "TELEGRAPH_VAULT_PASSWORD", "") or "").strip()
    if password:
        return password
    # Dev fallback: run/telegraph_*.json bundle written by a reset script.
    try:
        from toto.conf import run_dir as _run_dir
        run_dir = _run_dir()
        for bundle in run_dir.glob("telegraph_*.json"):
            try:
                vp = json.loads(bundle.read_text()).get("vault_password", "")
                if vp:
                    return vp
            except Exception:
                pass
    except Exception:
        pass
    raise VaultUnavailable(
        "TELEGRAPH_VAULT_PASSWORD is not set. Run `manage.py telegraph_init_vault` and "
        "configure this environment variable to enable encrypted message history."
    )


def system_strongbox():
    from toto.gervazy.models import UserStrongbox

    return (
        UserStrongbox.objects.filter(name=SYSTEM_STRONGBOX_NAME).order_by("id").first()
    )


# Cache the unlocked session per strongbox pk so Argon2id runs once per process, not
# per message. Sessions are not shared across threads for *mutation*; the only mutation
# is idempotent DEK-cache warming, guarded by a lock.
_session_cache: dict[int, GervazyCryptoSession] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Drop the cached session (used by tests that re-create the strongbox)."""
    with _lock:
        _session_cache.clear()


def open_session() -> GervazyCryptoSession:
    sb = system_strongbox()
    if not sb:
        raise VaultUnavailable(
            "Telegraph vault not initialized. Run: manage.py telegraph_init_vault"
        )
    with _lock:
        session = _session_cache.get(sb.pk)
        if session is None:
            session = GervazyCryptoSession(sb, load_vault_password())
            _session_cache[sb.pk] = session
        return session


def is_available() -> bool:
    try:
        open_session()
        return True
    except Exception:
        return False


# ── per-channel data key ────────────────────────────────────────────────────────

def channel_wrapped_key(channel, session=None):
    """Return the channel's active ``WrappedDataKey``, creating it on first use."""
    session = session or open_session()
    if channel.dek_id and channel.dek and channel.dek.state == "active":
        return channel.dek
    wrapped_key = session.create_data_key()
    channel.dek = wrapped_key
    channel.save(update_fields=["dek"])
    return wrapped_key


def _aad(channel, message_id) -> bytes:
    """Bind a ciphertext to its channel + message id (anti-replay across rows)."""
    return f"telegraph:{channel.slug}:{message_id}".encode("utf-8")


# ── message encrypt / decrypt / store ─────────────────────────────────────────────

def store_message(channel, *, msg_type, payload: dict, sender=None,
                  sender_name="", sender_avatar_url=""):
    """Encrypt ``payload`` under the channel DEK and persist a ``TelegraphMessage``.

    Returns the saved row (with ``expires_at`` set from the channel TTL). Raises
    :class:`VaultUnavailable` if the vault is not configured — the caller then keeps
    the live broadcast but skips persistence.
    """
    import uuid

    from .models import TelegraphMessage

    session = open_session()
    wrapped_key = channel_wrapped_key(channel, session)

    message_id = uuid.uuid4()
    aad = _aad(channel, message_id)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ciphertext, nonce = session.encrypt_blob(wrapped_key, plaintext, aad)

    now = timezone.now()
    return TelegraphMessage.objects.create(
        id=message_id,
        channel=channel,
        sender=sender,
        sender_name=sender_name,
        sender_avatar_url=sender_avatar_url,
        msg_type=msg_type,
        ciphertext=ciphertext,
        nonce=nonce,
        aad=aad,
        created_at=now,
        expires_at=now + channel.message_ttl,
    )


def store_e2e_message(channel, *, pin_key_id, iv, ciphertext, sender=None,
                      sender_name="", sender_avatar_url=""):
    """Persist a "secure-on-send" message: end-to-end encrypted on the client under the
    member-held pin key. The server stores only opaque bytes — no DEK, no plaintext, and
    no vault password required (it cannot read this message). Returns the saved row.
    """
    from .models import TelegraphMessage

    now = timezone.now()
    return TelegraphMessage.objects.create(
        channel=channel,
        sender=sender,
        sender_name=sender_name,
        sender_avatar_url=sender_avatar_url,
        msg_type="chat_message",
        encryption="e2e",
        pin_key_id=pin_key_id,
        iv=iv,
        ciphertext=ciphertext,
        nonce=b"",  # unused for e2e (no DEK envelope)
        aad=b"",
        created_at=now,
        expires_at=now + channel.message_ttl,
    )


def decrypt_message(row) -> dict:
    """Decrypt a ``TelegraphMessage`` row back into its payload dict."""
    session = open_session()
    raw = session.decrypt_blob(row.channel.dek, row.ciphertext, row.nonce, bytes(row.aad or b""))
    return json.loads(raw.decode("utf-8"))


def history(channel, *, limit=200, now=None):
    """Return the newest non-expired messages (oldest-first) as render-ready dicts.

    Each dict carries the decrypted payload plus stable ``id``/``created_at``/sender
    fields so a client can render and dedup history identically to live messages.
    """
    now = now or timezone.now()
    rows = list(
        channel.messages.filter(expires_at__gt=now).order_by("-created_at")[:limit]
    )
    rows.reverse()  # oldest-first for natural append
    session = None  # opened lazily, only when an at-rest row needs decrypting
    out = []
    for row in rows:
        # "secure-on-send" (e2e): the server can't read it — replay the opaque ciphertext
        # and let the client decrypt with its member-held pin key. No vault password needed.
        if row.encryption == "e2e":
            out.append({
                "type": "secure_message",
                "id": str(row.id),
                "user": row.sender_name,
                "avatar_url": row.sender_avatar_url,
                "created_at": row.created_at.isoformat(),
                "pin_key_id": row.pin_key_id,
                "iv": base64.b64encode(bytes(row.iv or b"")).decode(),
                "ciphertext": base64.b64encode(bytes(row.ciphertext)).decode(),
                "history": True,
            })
            continue
        try:
            if session is None:
                session = open_session()
            payload = json.loads(
                session.decrypt_blob(
                    channel.dek, row.ciphertext, row.nonce, bytes(row.aad or b"")
                ).decode("utf-8")
            )
        except VaultUnavailable:
            continue  # vault not configured — skip at-rest rows, still serve e2e ones
        except Exception:
            log.warning("telegraph: failed to decrypt history row %s", row.id)
            continue
        payload.update(
            {
                "type": row.msg_type,
                "id": str(row.id),
                "user": row.sender_name,
                "avatar_url": row.sender_avatar_url,
                "created_at": row.created_at.isoformat(),
                "history": True,
            }
        )
        out.append(payload)
    return out


def purge_expired(channel=None, *, now=None) -> int:
    """Delete expired messages (optionally scoped to one channel). Returns the count.

    Both at-rest and end-to-end (secure-on-send) rows share the same TTL.
    """
    from .models import TelegraphMessage

    now = now or timezone.now()
    qs = TelegraphMessage.objects.filter(expires_at__lte=now)
    if channel is not None:
        qs = qs.filter(channel=channel)
    deleted, _ = qs.delete()
    return deleted
