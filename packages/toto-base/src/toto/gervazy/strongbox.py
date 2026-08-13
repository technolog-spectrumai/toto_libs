"""One service strongbox, parameterised — the extraction the third copy asked for.

Three apps carry a near-identical 150-line ``vault.py``: ``gervazy/vault.py``
(the ``sabbia-system`` box), ``sso_core/vault.py`` and ``jess/vault.py``. Each
one is the same envelope with two strings changed. ``sso_core/vault.py:13-17``
wrote down what should happen when a fourth arrived::

    jess/vault.py's docstring says a fourth instance should trigger extracting a
    shared Strongbox(name, password_setting) into gervazy; moving rather than
    duplicating keeps the count at three and leaves that extraction as a clean
    future change rather than a risky mid-feature one.

``toto.steven`` is the fourth. This is that extraction, and steven is its only
consumer today — **the three existing copies are deliberately left alone**.
Porting them is a change with its own blast radius (three passphrases, three
sets of tests, one of them holding the SSO signing key) and it does not belong
inside a feature.

## The shape, and why it is server-side

    <APP>_VAULT_PASSWORD ─Argon2id▶ UKEK ─unwrap▶ VMK ─unwrap▶ DEK ─AES-GCM▶ secret

No human in the loop: a celery worker running outside any request has to be able
to resolve a credential. That is the same reason sabbia and jess unlock this way
and the opposite of ``gervazy.signing``, where a person types their own
passphrase per operation.

## Losing the passphrase is unrecoverable

There is no escrow and no recovery path — that is what encryption at rest means.
``scripts/deploy.py`` mints each ``*_VAULT_PASSWORD`` once and reuses the
existing value on every redeploy for exactly this reason, and
``scripts/test_deploy.py`` guards it in three lines. **A new consumer MUST add
its variable to both**, or its secrets die at the next deploy.

## Every failure is VaultUnavailable

A typed error callers are expected to RECORD rather than propagate. A missing
passphrase, a locked box, a secret copied from another instance and a wrong
``*_VAULT_PASSWORD`` all look identical from here, so the message names them.
"""

from __future__ import annotations

import threading
import uuid

from django.conf import settings
from django.db import transaction

from .crypto import GervazyCryptoSession


class VaultUnavailable(RuntimeError):
    """This strongbox cannot be used: no passphrase configured, or not initialised."""


class Strongbox:
    """A named service strongbox unlocked from one setting.

    Instantiate once at module scope in the consuming app::

        vault = Strongbox(
            name="steven-system",
            owner_username="steven-vault",
            password_setting="STEVEN_VAULT_PASSWORD",
            label="Steven",
        )

    and call ``vault.store_secret(...)`` / ``vault.read_secret(...)`` from there.
    """

    def __init__(self, *, name: str, owner_username: str, password_setting: str,
                 label: str = ""):
        self.name = name
        self.owner_username = owner_username
        self.password_setting = password_setting
        self.label = label or name
        # Argon2id at 64 MiB / 3 iterations is deliberately expensive, so the
        # unlocked session is cached per strongbox pk and the derivation runs
        # once per process. Same approach as gervazy/vault.py and jess/vault.py.
        self._session_cache: dict[int, GervazyCryptoSession] = {}
        self._lock = threading.Lock()

    # -- the passphrase -----------------------------------------------------

    def load_password(self) -> str:
        password = (getattr(settings, self.password_setting, "") or "").strip()
        if not password:
            raise VaultUnavailable(
                f"{self.password_setting} is not set, so {self.label} cannot "
                f"decrypt its stored secret. Set it in the host's environment "
                f"(deploy.py mints and preserves it), then re-save the secret."
            )
        return password

    def clear_cache(self) -> None:
        """Drop the cached session. Tests that re-create the box must call this."""
        with self._lock:
            self._session_cache.clear()

    # -- the box ------------------------------------------------------------

    def existing(self):
        from .models import UserStrongbox

        return (UserStrongbox.objects.filter(name=self.name)
                .order_by("id").first())

    def _get_or_create_owner(self):
        """The service account that owns the box.

        Not a login: it exists only to own a strongbox, so its password is
        unusable even if ``is_active`` were ever flipped.
        """
        from django.contrib.auth import get_user_model

        User = get_user_model()
        owner, created = User.objects.get_or_create(
            username=self.owner_username, defaults={"is_active": False})
        if created:
            owner.set_unusable_password()
            owner.save(update_fields=["password"])
        return owner

    def ensure(self):
        """The strongbox, created on first use if absent.

        Auto-provisioning rather than requiring a management command: the
        passphrase is already guaranteed by deploy.py, so there is nothing for a
        human to decide. Idempotent and race-safe — the box is re-read inside the
        transaction, so two workers starting at once cannot make two.
        """
        password = self.load_password()
        with transaction.atomic():
            box = self.existing()
            if box is not None:
                return box
            owner = self._get_or_create_owner()
            GervazyCryptoSession.initialize_strongbox(owner, self.name, password)
            return self.existing()

    def open_session(self, *, create: bool = True) -> GervazyCryptoSession:
        """An unlocked session, creating the box if needed.

        ``create=False`` is the read-only path: provisioning a strongbox — a
        write, inside a transaction — as a side effect of rendering a GET is how
        a page render becomes a deadlock under load. A host with no box has no
        secret to read either, so refusing is also the right answer.
        """
        box = self.ensure() if create else self.existing()
        if box is None:
            raise VaultUnavailable(
                f"{self.label}'s strongbox does not exist yet."
                if not create else
                f"{self.label}'s strongbox could not be created.")
        with self._lock:
            session = self._session_cache.get(box.pk)
            if session is None:
                session = GervazyCryptoSession(box, self.load_password())
                self._session_cache[box.pk] = session
            return session

    def is_available(self) -> bool:
        """Can the box be opened at all? Never raises.

        **Does NOT prove a secret decrypts.** Constructing a session only runs
        the KDF — a wrong passphrase builds a perfectly happy session and fails
        later as ``InvalidTag`` on the first unwrap. Use ``read_secret`` when the
        question is "will this actually work".
        """
        try:
            self.open_session(create=False)
            return True
        except Exception:  # noqa: BLE001 - a status probe never raises
            return False

    # -- secrets ------------------------------------------------------------

    def unique_secret_name(self, prefix: str) -> str:
        """A box-unique name. ``EncryptedSecret.name`` is unique per strongbox,
        so rotating cannot reuse a name while the old row still exists."""
        return f"{prefix}-{uuid.uuid4().hex[:12]}"

    def store_secret(self, plaintext: str, *, name: str, purpose: str = "",
                     session: GervazyCryptoSession | None = None):
        """Encrypt into this box; return the ``EncryptedSecret``.

        Reuses the box's active data key, creating one if none exists.
        """
        session = session or self.open_session()
        box = self.ensure()
        wrapped = box.data_keys.filter(state="active").order_by("id").first()
        if wrapped is None:
            wrapped = session.create_data_key()
        return session.encrypt_secret(wrapped, plaintext, name=name, purpose=purpose)

    def read_secret(self, secret, *, session: GervazyCryptoSession | None = None,
                    create: bool = True) -> str:
        """Decrypt a stored secret, or ``VaultUnavailable``.

        **The row is re-read, and that is not defensive padding.** Unwrapping
        walks ``secret → wrapped_key → vmk``, and Django caches those related
        objects on the instance. A passphrase rotation rewrites
        ``VaultMasterKey.encrypted_vmk`` and its nonce in place, so ANY object
        graph fetched before the rotation still carries the old ciphertext and
        fails as ``InvalidTag`` under the new passphrase — while a freshly
        fetched one decrypts perfectly. That is a genuinely confusing failure
        (the passphrase is right, the data is right, the object is stale), and
        the three hand-written vaults avoid it only because their callers happen
        to re-read per use. One query buys the guarantee outright.

        The ``InvalidTag`` translation is deliberate: a wrong passphrase, a
        changed ``*_VAULT_PASSWORD`` and a secret copied from another instance
        all surface as the same cryptographic failure, and a caller cannot tell
        them apart — so the message names all three.
        """
        if secret is None:
            raise VaultUnavailable("No secret is stored.")
        try:
            from .models import EncryptedSecret

            fresh = (EncryptedSecret.objects
                     .select_related("wrapped_key__vmk", "strongbox")
                     .filter(pk=secret.pk).first())
            return (session or self.open_session(create=create)).decrypt_secret(
                fresh or secret)
        except VaultUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001
            raise VaultUnavailable(
                f"{self.label} could not decrypt the stored secret "
                f"({type(exc).__name__}). A wrong passphrase, a changed "
                f"{self.password_setting}, or a secret copied from another "
                f"instance all look like this."
            ) from exc

    def reencrypt_secret(self, secret, *, name: str):
        """Re-wrap a secret under a freshly minted data key — per-secret rotation.

        Returns a NEW ``EncryptedSecret``; the caller repoints its FK and then
        retires the old one, **in that order**. A retired data key's secrets are
        unreadable through the session API, permanently.
        """
        session = self.open_session()
        plaintext = session.decrypt_secret(secret)
        wrapped = session.create_data_key()
        return session.encrypt_secret(wrapped, plaintext, name=name,
                                      purpose=secret.purpose)

    def retire_secret(self, secret) -> None:
        """Mark it retired, and its data key too if nothing active still uses it."""
        if secret is None:
            return
        if secret.state == "active":
            secret.state = "retired"
            secret.save()
        dek = secret.wrapped_key
        if dek and dek.state == "active" and not dek.secrets.filter(state="active").exists():
            dek.state = "retired"
            dek.save()

    def rotate_passphrase(self, old_password: str, new_password: str, *, actor=None) -> None:
        """Change the passphrase. Nothing below the master key is touched.

        Re-derives the UKEK under a fresh salt and re-wraps only the master
        key(s); every data key and every stored secret is left byte-for-byte
        unchanged, and nothing is decrypted. The old passphrase is verified
        first — a wrong one changes nothing.
        """
        if not new_password:
            raise VaultUnavailable("The new passphrase cannot be empty.")
        box = self.existing()
        if box is None:
            raise VaultUnavailable(f"{self.label}'s vault has not been set up yet.")
        GervazyCryptoSession.rewrap_master_keys(box, old_password, new_password)
        self.clear_cache()
        self.log_secret_event(actor, "rotate_vault_passphrase", None,
                              reason=f"rotated the {self.name} passphrase")

    # -- the audit trail ----------------------------------------------------

    def log_secret_event(self, actor, action: str, secret, *, success: bool = True,
                         reason: str = "") -> None:
        """Append to ``CryptoAuditLog``. Never raises, never carries a value.

        ``CryptoAuditLog.save()`` REJECTS a reason containing "password=",
        "secret=", "token=", "private_key" or "smtp_password" — so a reason that
        merely names a purpose string verbatim can raise. Swallowed here: an
        audit write must not be what breaks a credential update.
        """
        try:
            from .models import CryptoAuditLog

            CryptoAuditLog.objects.create(
                actor=actor if getattr(actor, "is_authenticated", False) else None,
                action=action,
                success=success,
                reason=reason[:500],
                object_type="EncryptedSecret" if secret is not None else "",
                object_id=str(getattr(secret, "pk", "") or ""),
            )
        except Exception:  # noqa: BLE001
            return
