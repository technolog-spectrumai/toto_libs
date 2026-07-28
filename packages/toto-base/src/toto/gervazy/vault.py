"""Sabbia at-rest credential vault.

Agent API keys (e.g. the OpenAI key for the Steven bot) are stored encrypted in
Gervazy under a single *sabbia system strongbox*, unlocked server-side by
``SABBIA_VAULT_PASSWORD`` — no human in the loop. This mirrors
``toto.sso_master``'s signing vault and lets the websocket consumer
(which runs outside any request) resolve credentials.

    SABBIA_VAULT_PASSWORD ─Argon2id▶ UKEK ─unwrap▶ VMK ─unwrap▶ DEK ─AES-GCM▶ secret
"""
import json
import threading
from pathlib import Path

from django.conf import settings

from toto.gervazy.crypto import GervazyCryptoSession

# The single service strongbox that owns every agent credential.
SYSTEM_STRONGBOX_NAME = "sabbia-system"
# Username of the service account that owns the strongbox.
SYSTEM_OWNER_USERNAME = "sabbia-vault"


class VaultUnavailable(RuntimeError):
    """The sabbia vault is not usable (no password configured, or not initialized)."""


def load_vault_password() -> str:
    password = (getattr(settings, "SABBIA_VAULT_PASSWORD", "") or "").strip()
    if password:
        return password
    # Dev fallback: run/sabbia_*.json bundle written by a reset script.
    try:
        from toto.conf import run_dir as _run_dir
        run_dir = _run_dir()
        for bundle in run_dir.glob("sabbia_*.json"):
            try:
                vp = json.loads(bundle.read_text()).get("vault_password", "")
                if vp:
                    return vp
            except Exception:
                pass
    except Exception:
        pass
    raise VaultUnavailable(
        "SABBIA_VAULT_PASSWORD is not set. Run `manage.py sabbia_init_vault` and "
        "configure this environment variable to enable encrypted agent credentials."
    )


def system_strongbox():
    from toto.gervazy.models import UserStrongbox

    return (
        UserStrongbox.objects.filter(name=SYSTEM_STRONGBOX_NAME).order_by("id").first()
    )


# Cache the unlocked session per strongbox pk so Argon2id runs once per process.
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
            "Sabbia vault not initialized. Run: manage.py sabbia_init_vault"
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


def store_secret(plaintext: str, *, name: str, purpose: str = ""):
    """Encrypt ``plaintext`` into the sabbia system strongbox; return the EncryptedSecret.

    Reuses the strongbox's active data key, creating one if none exists.
    """
    session = open_session()
    sb = system_strongbox()
    wrapped_key = sb.data_keys.filter(state="active").order_by("id").first()
    if wrapped_key is None:
        wrapped_key = session.create_data_key()
    return session.encrypt_secret(wrapped_key, plaintext, name=name, purpose=purpose)


def reencrypt_secret(secret, *, name: str):
    """Re-wrap an existing secret's value under a freshly created data key.

    Decrypts the current value, mints a NEW DEK, and re-encrypts the SAME value
    under it — per-secret key rotation. Returns the new EncryptedSecret (``name``
    must be unique within the strongbox); the caller repoints + retires the old.
    """
    session = open_session()
    plaintext = session.decrypt_secret(secret)
    new_dek = session.create_data_key()
    return session.encrypt_secret(new_dek, plaintext, name=name, purpose=secret.purpose)


def retire_secret(secret) -> None:
    """Mark ``secret`` retired; also retire its data key if no active secret uses it."""
    if secret is None:
        return
    if secret.state == "active":
        secret.state = "retired"
        secret.save()
    dek = secret.wrapped_key
    if dek and dek.state == "active" and not dek.secrets.filter(state="active").exists():
        dek.state = "retired"
        dek.save()


def log_secret_event(actor, action: str, secret, *, success: bool = True, reason: str = "") -> None:
    """Append a CryptoAuditLog entry for a secret operation. Never logs plaintext.

    Best-effort: auditing must never break the operation it records.
    """
    from toto.gervazy.models import CryptoAuditLog

    try:
        CryptoAuditLog.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            strongbox=system_strongbox(),
            action=action,
            object_type="EncryptedSecret",
            object_id=str(getattr(secret, "pk", "") or ""),
            success=success,
            reason=reason,
        )
    except Exception:  # noqa: BLE001 — audit failure must not abort the rotation
        pass
