"""The SSO domain's strongbox, reachable from both sides of a federation.

    SSO_VAULT_PASSWORD ─Argon2id▶ UKEK ─unwrap▶ VMK ─unwrap▶ DEK ─AES-GCM▶ secret

**Why this lives in ``sso_core``.** The provider already had these helpers, private
to ``sso_master/services.py``, for the signing key's private half. The consumer now
needs the same strongbox for its client secret — and ``sso_master`` is not
installed on a consumer host (``auth_config.auth_apps``), so it could not import
them. ``sso_core`` is the one auth app installed in **both** modes, exactly as
``password_reset.py`` is, so the helpers moved here and ``sso_master`` imports them
back. One implementation, both sides.

This is deliberately *not* a fourth copy of the pattern. ``jess/vault.py``'s
docstring says a fourth instance should trigger extracting a shared
``Strongbox(name, password_setting)`` into gervazy; moving rather than duplicating
keeps the count at three (sabbia, SSO, jess) and leaves that extraction as a clean
future change rather than a risky mid-feature one.

**Same passphrase, same box, on purpose.** A consumer's client secret and a
provider's signing key are both SSO credentials, so they share ``SSO_VAULT_PASSWORD``
and the ``sso-system-strongbox`` rather than inventing a fourth environment
variable a host would have to learn about. No host is ever both.

**Losing the passphrase is unrecoverable.** ``deploy.py`` mints it once and reuses
the existing value on every redeploy for exactly this reason.
"""
from __future__ import annotations

import threading

from django.conf import settings
from django.db import transaction

from toto.gervazy.crypto import GervazyCryptoSession

# The name the provider's signing key has always used
# (``create_sso_signing_key.py:25``). Shared so a host that is later converted
# between modes finds the box it already has.
SSO_STRONGBOX_NAME = "sso-system-strongbox"
# Owns the box on a consumer, which has no signing key and so never ran
# create_sso_signing_key. Cannot log in — see ensure_strongbox.
SSO_OWNER_USERNAME = "sso-vault"

PURPOSE_CLIENT_SECRET = "oidc_client_secret"


class VaultUnavailable(RuntimeError):
    """The SSO vault cannot be used: no passphrase, or not initialised.

    Typed so callers can *record* it rather than propagate it — a failed pairing
    should say "the vault is locked" on the page, not 500.
    """


def load_vault_password() -> str:
    """The passphrase, or ``VaultUnavailable``.

    Moved verbatim from ``sso_master/services.py`` including its ``run/sso_*.json``
    dev fallback, which exists because that bundle predates deploy.py minting
    anything. ``services.py`` now calls this.
    """
    password = (getattr(settings, "SSO_VAULT_PASSWORD", "") or "").strip()
    if password:
        return password

    import json

    from toto.conf import run_dir as _run_dir

    for bundle_path in _run_dir().glob("sso_*.json"):
        try:
            vp = json.loads(bundle_path.read_text()).get("vault_password", "")
            if vp:
                return vp
        except Exception:                       # noqa: BLE001 — a bad bundle is not fatal
            pass

    raise VaultUnavailable(
        "SSO_VAULT_PASSWORD is not set, so this platform cannot open its SSO "
        "vault. Set it in the host's environment (deploy.py mints and preserves "
        "it), then pair again."
    )


def system_strongbox():
    from toto.gervazy.models import UserStrongbox

    return (
        UserStrongbox.objects.filter(name=SSO_STRONGBOX_NAME).order_by("id").first()
    )


# Argon2id is deliberately expensive, so the unlocked session is cached per
# strongbox pk and the derivation runs once per process — the same approach as
# gervazy/vault.py. Note sso_master's signing path does NOT cache and pays ~68 ms
# per ID token; that is a separate, known cost.
_session_cache: dict[int, GervazyCryptoSession] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Drop the cached session. Tests re-creating the strongbox must call this."""
    with _lock:
        _session_cache.clear()


def ensure_strongbox():
    """The SSO strongbox, created on first use if absent.

    A provider host already has one, made by ``create_sso_signing_key``. A
    consumer host has never had a reason to, so this creates it at pairing time
    rather than making an operator run a command first.

    Idempotent and race-safe: the owner is fetched-or-created and the box re-read
    inside the transaction, so two simultaneous pairings cannot make two.
    """
    from django.contrib.auth import get_user_model

    password = load_vault_password()

    with transaction.atomic():
        existing = system_strongbox()
        if existing is not None:
            return existing

        User = get_user_model()
        owner, created = User.objects.get_or_create(
            username=SSO_OWNER_USERNAME, defaults={"is_active": False},
        )
        if created:
            # Not a login: this account exists only to own a strongbox. An
            # unusable password means it cannot authenticate even if someone
            # later flips is_active.
            owner.set_unusable_password()
            owner.save(update_fields=["password"])

        GervazyCryptoSession.initialize_strongbox(owner, SSO_STRONGBOX_NAME, password)
        return system_strongbox()


def open_session(*, create: bool = True) -> GervazyCryptoSession:
    """An unlocked session against the SSO strongbox.

    ``create=False`` is the read-only path, for callers that run while rendering a
    page: provisioning a strongbox is a write inside a transaction, and doing that
    as a side effect of a GET is how a page render becomes a deadlock under load.
    """
    box = ensure_strongbox() if create else system_strongbox()
    if box is None:
        raise VaultUnavailable(
            "The SSO strongbox does not exist yet on this platform."
            if not create else
            "The SSO strongbox could not be created."
        )
    with _lock:
        session = _session_cache.get(box.pk)
        if session is None:
            session = GervazyCryptoSession(box, load_vault_password())
            _session_cache[box.pk] = session
        return session


def is_available() -> bool:
    """Can the vault be opened at all? Never raises.

    Note this does NOT prove a given secret decrypts: a wrong passphrase against
    an existing strongbox is not detected at session construction — only the KDF
    runs there — and surfaces later as an ``InvalidTag`` from the unwrap.
    """
    try:
        open_session()
        return True
    except Exception:                           # noqa: BLE001
        return False


def store_secret(plaintext: str, *, name: str, purpose: str = PURPOSE_CLIENT_SECRET):
    """Encrypt ``plaintext`` into the SSO strongbox and return the EncryptedSecret."""
    session = open_session()
    box = system_strongbox()
    wrapped_key = box.data_keys.filter(state="active").order_by("id").first()
    if wrapped_key is None:
        wrapped_key = session.create_data_key()
    return session.encrypt_secret(wrapped_key, plaintext, name=name, purpose=purpose)


def read_secret(secret, *, create: bool = True) -> str:
    """Decrypt a stored secret, translating AEAD failures into ``VaultUnavailable``.

    A wrong passphrase, or a row copied from another install (``EncryptedSecret``
    binds its own pk into the AAD), both surface as ``InvalidTag``. The caller's
    job is to record a legible failure, not to know about AEAD.
    """
    if secret is None:
        raise VaultUnavailable("No secret is stored for this connection.")
    try:
        return open_session(create=create).decrypt_secret(secret)
    except VaultUnavailable:
        raise
    except Exception as exc:                    # noqa: BLE001
        raise VaultUnavailable(
            f"The stored client secret could not be decrypted ({type(exc).__name__}). "
            "A changed SSO_VAULT_PASSWORD, or a row copied from another install, "
            "both look like this. Pair again to store a fresh secret."
        ) from exc


def retire_secret(secret) -> None:
    """Mark ``secret`` retired, and its data key too if nothing active uses it."""
    if secret is None:
        return
    if secret.state == "active":
        secret.state = "retired"
        secret.save()
    dek = secret.wrapped_key
    if dek and dek.state == "active" and not dek.secrets.filter(state="active").exists():
        dek.state = "retired"
        dek.save()


def unique_secret_name(prefix: str = "oidc-client") -> str:
    """A strongbox-unique name. ``EncryptedSecret.name`` is unique per box, so a
    re-pair cannot reuse the old name while the old row still exists."""
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:12]}"
