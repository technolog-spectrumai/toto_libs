"""Sabbia at-rest credential vault.

Agent API keys (e.g. the OpenAI key for the Steven bot) are stored encrypted in
Gervazy under a single *sabbia system strongbox*, unlocked server-side by
``SABBIA_VAULT_PASSWORD`` — no human in the loop. This mirrors
``toto.telegraph.vault`` / ``toto.sso_master`` and lets the websocket consumer
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
        run_dir = Path(settings.BASE_DIR).parent / "run"
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
