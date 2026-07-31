"""Jess's at-rest credential vault — its own strongbox, its own passphrase.

    JESS_VAULT_PASSWORD ─Argon2id▶ UKEK ─unwrap▶ VMK ─unwrap▶ DEK ─AES-GCM▶ secret

Same four-tier gervazy envelope that protects the SSO signing key's private half, and
the same server-side-unlock shape as ``toto.gervazy.vault``: no human in the loop, so a
Celery worker running outside any request can resolve an SMTP password.

**Why this is a sibling of ``gervazy/vault.py`` rather than a call into it.** That module
is hardcoded to the ``sabbia-system`` strongbox and ``SABBIA_VAULT_PASSWORD``
(``gervazy/vault.py:20-22``), a name inherited from a retired AI app. Its existing callers
— ``api/admin.py`` and ``toto.connectors`` — all assume that one box, so widening its
signature to serve two would put them at risk for no benefit here. Jess gets its own box
instead: one blast radius per secret domain, and no legacy name leaking into a new app's
configuration.

**Losing the passphrase is unrecoverable.** ``portal/scripts/deploy.py`` mints
``JESS_VAULT_PASSWORD`` once and reuses the existing value on every redeploy for exactly
this reason. There is no escrow and no recovery path — that is what encryption at rest
means.

Every failure is ``VaultUnavailable``, a typed error callers are expected to *record*
rather than propagate: a send becomes a ``failed`` MailMessage naming the passphrase, and
``can_deliver()`` goes False so the platform stops advertising password reset. Compare
``sso_master/services.py:76``, which raises the equivalent into a token exchange and
500s.
"""
from __future__ import annotations

import threading

from django.conf import settings
from django.db import transaction

from toto.gervazy.crypto import GervazyCryptoSession

# Jess's own service strongbox and the service account that owns it. Distinct from
# gervazy's "sabbia-system" box on purpose — see the module docstring.
SYSTEM_STRONGBOX_NAME = "jess-system"
SYSTEM_OWNER_USERNAME = "jess-vault"

# What a stored SMTP password is tagged as, for the audit log and the admin display.
# gervazy's EncryptedSecret.purpose help_text names this exact string as an example
# (gervazy/models.py:286).
PURPOSE_SMTP_PASSWORD = "smtp_password"


class VaultUnavailable(RuntimeError):
    """Jess's vault cannot be used: no passphrase configured, or not initialised."""


def manual_release_enabled() -> bool:
    """Is this host in manual-release custody mode?

    The one source of truth for the whole app. In manual mode there is NO ambient
    passphrase: nothing decrypts without an admin typing it, and mail queues as
    ``held`` rather than being dispatched. Read from settings so a host opts in with
    ``JESS_MANUAL_RELEASE`` in its env and tests can ``override_settings``.
    """
    return bool(getattr(settings, "JESS_MANUAL_RELEASE", False))


def load_vault_password() -> str:
    """The ambient passphrase, or ``VaultUnavailable``.

    **Fails closed in manual-release mode.** There is deliberately no server-side
    passphrase then — an admin types it per operation and it is discarded — so every
    caller that would reach for an ambient one (``open_session``, ``ensure_strongbox``,
    ``is_available``) fails closed here rather than silently reading a key that, by
    design, is not supposed to exist on the box.

    No dev fallback to a ``run/*.json`` bundle, unlike ``gervazy/vault.py:33-45`` and
    ``sso_master/services.py:65-75``. Those exist because their strongboxes predate
    ``deploy.py`` minting anything; Jess's passphrase is minted from the start, so a
    second provenance would only be a second thing to get out of step.
    """
    if manual_release_enabled():
        raise VaultUnavailable(
            "Jess is in manual-release mode: there is no passphrase on this server. "
            "An admin must type it to send or to read a stored secret."
        )
    password = (getattr(settings, "JESS_VAULT_PASSWORD", "") or "").strip()
    if not password:
        raise VaultUnavailable(
            "JESS_VAULT_PASSWORD is not set, so Jess cannot decrypt an SMTP password. "
            "Set it in the host's environment (deploy.py mints and preserves it), then "
            "re-save the provider's password in the admin."
        )
    return password


def system_strongbox():
    from toto.gervazy.models import UserStrongbox

    return (
        UserStrongbox.objects.filter(name=SYSTEM_STRONGBOX_NAME).order_by("id").first()
    )


# Argon2id at 64 MiB / 3 iterations is deliberately expensive, so the unlocked session
# is cached per strongbox pk and the derivation runs once per process. Same approach as
# gervazy/vault.py:60-62 — and note sso_master does NOT do this, which is why it pays
# ~68 ms per ID token (services.py:190-214).
_session_cache: dict[int, GervazyCryptoSession] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    """Drop the cached session. Tests that re-create the strongbox must call this."""
    with _lock:
        _session_cache.clear()


def _get_or_create_owner():
    """The service account that owns Jess's strongbox.

    Not a login: it exists only to own a strongbox, so its password is unusable even
    if ``is_active`` were ever flipped. Factored out so the manual-mode setup flow
    (``views.vault_setup``) can create the strongbox itself, since ``ensure_strongbox``
    refuses to auto-provision when there is no ambient passphrase.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    owner, created = User.objects.get_or_create(
        username=SYSTEM_OWNER_USERNAME,
        defaults={"is_active": False},
    )
    if created:
        owner.set_unusable_password()
        owner.save(update_fields=["password"])
    return owner


def ensure_strongbox():
    """The strongbox, created on first use if absent.

    Auto-provisioning rather than requiring a management command first: the passphrase
    is already guaranteed by deploy.py, so there is nothing for a human to decide, and
    ``ingress_sso_master`` sets the precedent by minting the signing key unprompted
    (``ingress_sso_master.py:37-43``). ``manage.py jess_init_vault`` exists to do it
    explicitly and to report the result, not because it is required.

    **Refuses in manual-release mode** — ``load_vault_password()`` raises there, because
    auto-provisioning needs an ambient passphrase and manual mode has none. A manual host
    initialises its strongbox through ``views.vault_setup`` with an admin-typed passphrase.

    Idempotent and racy-safe: the owner is fetched-or-created and the strongbox is
    re-read inside the transaction, so two workers starting at once cannot make two.
    """
    password = load_vault_password()

    with transaction.atomic():
        existing = system_strongbox()
        if existing is not None:
            return existing

        owner = _get_or_create_owner()
        _session, _wrapped = GervazyCryptoSession.initialize_strongbox(
            owner, SYSTEM_STRONGBOX_NAME, password
        )
        return system_strongbox()


def open_session(*, create: bool = True) -> GervazyCryptoSession:
    """An unlocked session against Jess's strongbox, creating it if needed.

    ``create=False`` is the read-only path, and it exists for one specific caller:
    ``status.can_deliver()`` runs while rendering an ANONYMOUS login page, and
    provisioning a strongbox — a write, inside a transaction — as a side effect of a GET
    is the kind of thing that turns a page render into a deadlock under load. A host with
    no strongbox has no secret Jess can read either, so refusing is also the right answer.
    """
    sb = ensure_strongbox() if create else system_strongbox()
    if sb is None:
        raise VaultUnavailable(
            "Jess's strongbox does not exist yet. Run: manage.py jess_init_vault"
            if not create else
            "Jess's strongbox could not be created."
        )
    with _lock:
        session = _session_cache.get(sb.pk)
        if session is None:
            session = GervazyCryptoSession(sb, load_vault_password())
            _session_cache[sb.pk] = session
        return session


def open_manual_session(passphrase: str) -> GervazyCryptoSession:
    """An unlocked session from an admin-TYPED passphrase, against the existing strongbox.

    The manual-release counterpart to ``open_session``. It builds a **fresh session on
    every call and never touches ``_session_cache``**, so the passphrase and the derived
    key live only for the duration of the caller's operation and are dropped when the
    session is garbage-collected (or ``.close()``d). This is the property the whole model
    rests on — there must be no long-lived unlocked session anywhere.

    Does not create the strongbox: a release must not provision as a side effect, and a
    host with no strongbox has nothing to read. Setup goes through ``views.vault_setup``.
    """
    if not passphrase:
        raise VaultUnavailable("A passphrase is required.")
    sb = system_strongbox()
    if sb is None:
        raise VaultUnavailable(
            "Jess's vault has not been set up yet. Initialise it first (Jess → set up "
            "the vault, or manage.py jess_init_vault)."
        )
    return GervazyCryptoSession(sb, passphrase)


def rotate_passphrase(old_password: str, new_password: str, *, actor=None) -> None:
    """Change the passphrase that unlocks Jess's strongbox.

    Re-derives the UKEK under a fresh salt and re-wraps only the master key(s); the data
    keys and every stored secret are untouched, so no SMTP password is re-encrypted and
    none is exposed. The old passphrase is verified first — a wrong one changes nothing.
    """
    if not new_password:
        raise VaultUnavailable("The new passphrase cannot be empty.")
    sb = system_strongbox()
    if sb is None:
        raise VaultUnavailable("Jess's vault has not been set up yet.")

    GervazyCryptoSession.rewrap_master_keys(sb, old_password, new_password)
    clear_cache()          # a non-manual host may hold a session under the old passphrase
    log_secret_event(
        actor, "rotate_vault_passphrase", None,
        reason="rotated the jess vault passphrase",
    )


def is_available() -> bool:
    """Can the vault be opened at all? Never raises.

    Note this does NOT prove a given secret decrypts: a wrong passphrase against an
    existing strongbox is not detected at session construction — only the KDF runs there
    (``gervazy/crypto.py:222-229``) — and surfaces later as ``InvalidTag`` from the
    unwrap. ``can_deliver()`` in status.py does the stronger check by actually reading
    the active provider's secret.
    """
    try:
        open_session()
        return True
    except Exception:
        return False


def store_secret(plaintext: str, *, name: str, purpose: str = PURPOSE_SMTP_PASSWORD,
                 session: GervazyCryptoSession | None = None):
    """Encrypt ``plaintext`` into Jess's strongbox and return the EncryptedSecret.

    ``name`` must be unique within the strongbox (``gervazy/models.py:319-328``), so
    callers append a uuid suffix — see ``unique_secret_name`` below.

    ``session`` lets a manual-mode caller inject a session built from a typed passphrase
    (``open_manual_session``); without it the ambient env session is used, which is the
    non-manual path and raises ``VaultUnavailable`` in manual mode.
    """
    session = session or open_session()
    sb = system_strongbox()
    wrapped_key = sb.data_keys.filter(state="active").order_by("id").first()
    if wrapped_key is None:
        wrapped_key = session.create_data_key()
    return session.encrypt_secret(wrapped_key, plaintext, name=name, purpose=purpose)


def read_secret(secret, *, session: GervazyCryptoSession | None = None,
                create: bool = True) -> str:
    """Decrypt a stored secret. Raises ``VaultUnavailable`` if the vault will not open.

    An ``InvalidTag`` from a wrong passphrase or a secret copied from another instance
    is translated too, because the caller's job is to record a legible failure rather
    than to know about AEAD — and in manual mode this is exactly how a wrong TYPED
    passphrase surfaces (the session builds fine; the unwrap fails here).

    ``session`` injects a typed-passphrase session (manual mode). Without it,
    ``create=False`` forwards to ``open_session`` — see its docstring for the one caller
    that needs it.
    """
    if secret is None:
        raise VaultUnavailable("No secret is stored for this provider.")
    try:
        return (session or open_session(create=create)).decrypt_secret(secret)
    except VaultUnavailable:
        raise
    except Exception as exc:
        raise VaultUnavailable(
            f"Jess could not decrypt the stored password ({type(exc).__name__}). "
            "A wrong passphrase, a changed JESS_VAULT_PASSWORD, or a secret copied from "
            "another instance all look like this."
        ) from exc


def reencrypt_secret(secret, *, name: str, session: GervazyCryptoSession | None = None):
    """Re-wrap the SAME value under a freshly minted data key — per-secret rotation.

    Returns the new EncryptedSecret; the caller repoints its row and retires the old one.
    ``session`` injects a typed-passphrase session (manual mode).
    """
    session = session or open_session()
    plaintext = session.decrypt_secret(secret)
    new_dek = session.create_data_key()
    return session.encrypt_secret(new_dek, plaintext, name=name, purpose=secret.purpose)


def retire_secret(secret) -> None:
    """Mark ``secret`` retired, and its data key too if nothing active still uses it."""
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
    """Append a CryptoAuditLog entry. Never logs plaintext; never breaks the caller.

    ``CryptoAuditLog.save()`` itself rejects credential-shaped text
    (``gervazy/models.py:667-681``), and "smtp_password" is on its forbidden-words list —
    so ``reason`` must stay a description, not a value.
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
    except Exception:
        pass


def unique_secret_name(prefix: str = "jess-smtp") -> str:
    """A strongbox-unique name for a new secret.

    ``EncryptedSecret.name`` is unique per strongbox, so rotating a password cannot
    reuse the old name while the old row still exists. Same uuid-suffix approach as
    ``api/admin.py:40-42``.
    """
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:12]}"
