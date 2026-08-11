"""Who is allowed to create assets, and how the platform knows.

Authority is a **keypair, not a setting**. `is_monetary_master()` means "the
local issuer row holds a usable private key" — there is no `ECONOMY_ROLE=master`
an operator can type, and no flag a branch can flip. A host either can produce a
signature the world will verify, or it cannot.

The private half is sealed under ``MONETARY_ISSUER_KEY``, a secret **deliberately
separate from FIELD_ENCRYPTION_KEY**. `toto.assets` is in `APPS_TO_SYNC`, so its
rows travel in backups; FIELD_ENCRYPTION_KEY travels in the deploy config. Had
the issuer key been sealed under it, restoring a production backup onto a
staging box would produce a second monetary master able to sign genesis
documents indistinguishable from the real ones. With a separate secret the
clone holds ciphertext it cannot open, `is_monetary_master()` answers False, and
it degrades to inert.

Everything here fails CLOSED. A missing key, an unreadable key, a key that does
not decrypt: not a master. The one thing that must never happen is a host
concluding it may issue because something threw and was swallowed the wrong way.
"""

from __future__ import annotations

import hashlib
import logging

log = logging.getLogger("toto.assets.issuer")

#: The setting naming the secret that seals the private half.
ISSUER_KEY_SETTING = "MONETARY_ISSUER_KEY"


class NotTheMaster(Exception):
    """This host cannot issue. The message says why, in words."""


def _issuer_fernet():
    """Fernet over MONETARY_ISSUER_KEY. Raises when it is absent or malformed.

    Deliberately NOT falling back to FIELD_ENCRYPTION_KEY: the whole point of a
    separate secret is that a host holding only the latter cannot open this.
    """
    from cryptography.fernet import Fernet
    from django.conf import settings

    secret = getattr(settings, ISSUER_KEY_SETTING, "")
    if not secret:
        raise NotTheMaster(
            f"{ISSUER_KEY_SETTING} is not set, so this host cannot open an "
            "issuer key. Only the monetary master carries it.")
    return Fernet(secret.encode() if isinstance(secret, str) else secret)


def fingerprint_for(public_key_pem: str) -> str:
    """A stable short name for an issuer: SHA-256 over its public key PEM.

    The fingerprint travels inside every genesis document, so it must be
    derived from the public half alone — anyone verifying a document has that
    and nothing else.
    """
    return hashlib.sha256(public_key_pem.encode()).hexdigest()


def local_issuer():
    """The issuer row describing *this* host, or None.

    Returns None when the table is not there yet — the app installed but not
    migrated, which happens during a deploy and during checks that run before
    migrate. The repo's standing answer to that shape (see toto.quota.rates):
    degrade, do not raise. Here it degrades the safe way, because "we cannot
    tell" and "not the master" lead to the same refusal.
    """
    from django.db import DatabaseError

    from .models import CurrencyIssuer

    try:
        return CurrencyIssuer.objects.filter(is_self=True).first()
    except DatabaseError:
        return None


def is_monetary_master() -> bool:
    """True iff this host holds a usable issuer private key.

    Every failure is False: no row, no key material, no secret to open it with,
    or ciphertext that will not decrypt. A host that cannot prove it is the
    master is not the master.
    """
    issuer = local_issuer()
    if issuer is None or not issuer.private_key_encrypted:
        return False
    try:
        issuer.private_key()
        return True
    except Exception:  # noqa: BLE001 - unusable key means not the master
        return False


def require_master(action: str = "issue assets") -> None:
    """Raise unless this host can sign as the issuer."""
    if not is_monetary_master():
        raise NotTheMaster(
            f"This platform cannot {action}: it holds no monetary issuer key. "
            "Assets are issued on the master platform and arrive here as "
            "signed mirrors.")


def mint_issuer(*, label: str) -> "object":
    """Create this host's issuer keypair. Idempotent — refuses a second one.

    NEVER call this from a migration. A migration runs on every host that
    migrates, so minting there would manufacture a monetary master on each one;
    that is why this lives behind a management command an operator runs once.
    """
    from .models import CurrencyIssuer

    existing = local_issuer()
    if existing is not None:
        raise NotTheMaster(
            f"This host already has an issuer ({existing.fingerprint[:12]}…). "
            "A second one would be a second monetary authority.")
    return CurrencyIssuer.objects.create_local(label=label)
