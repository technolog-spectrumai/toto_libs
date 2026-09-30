"""A member's own key store, created by the member (2026-10-01).

Until now a personal strongbox came into being only from the admin (a bare
``UserStrongbox`` row, no keys), the dev seed, or the desktop app's first
``GET /vault/api/strongbox/`` — and the web could only *initialise* one that
already existed (``views.initialize_strongbox_view``). This is the missing
door, used by My account: the member chooses a passphrase and gets a box with
its master key and first data key in one step, the way
``GervazyCryptoSession.initialize_strongbox`` makes every other box.

**One personal box, named ``strongbox``** — the name the desktop app's
get-or-create already gives it (``vault/api_views.py``), so the web door and
the desktop agree on which box is "yours". The purpose boxes (``mail``,
``vault-storage``, ``assets-wallet-pin``) are not it and do not block it.

**Never overwrites.** A box with that name — keyed or bare — refuses, and a
bare one is initialised on the keys page instead, which keeps its salt (the
desktop derives with it). Two concurrent creations cannot both win: the
owner+name unique constraint turns the second into the same refusal.

**Never moves the salt files are sealed under.** Eight call sites read
``user.user_strongboxes.first()`` (ordered by name) as THE salt for encrypted
vault files (see ``vault/storage_pin.py``). If a new box would become that
first box while the member already has another, their sealed files would stop
opening, so the creation is rolled back and refused.

**No recovery code.** The passphrase is the only way in: nothing is escrowed,
so there is no secret to show once and nothing to lose but the passphrase.
It is never stored, logged or put on either audit trail.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

#: The member's own box. Same string as the desktop app's get-or-create.
PERSONAL_STRONGBOX_NAME = "strongbox"

#: The shortest passphrase accepted — the storage PIN's rule
#: (``vault/storage_pin.py``), the one member-chosen strongbox secret the
#: platform already checks.
PASSPHRASE_MIN_LENGTH = 6


class KeyStoreExists(RuntimeError):
    """The member already has their personal key store."""


class KeyStoreRefused(RuntimeError):
    """Creating it here would move the salt the member's sealed files use."""


def personal_strongbox(user):
    """The member's personal box, keyed or bare, or None. Never creates one."""
    from .models import UserStrongbox

    return UserStrongbox.objects.filter(owner=user, name=PERSONAL_STRONGBOX_NAME).first()


def is_keyed(strongbox) -> bool:
    """Has it an active master key and an active data key — is it usable?"""
    return (strongbox.master_keys.filter(state="active").exists()
            and strongbox.data_keys.filter(state="active").exists())


def create_personal_strongbox(user, passphrase: str, *, request=None):
    """Make the member's key store with its first keys; return the box.

    Raises ``ValueError`` for a passphrase the rules refuse, ``KeyStoreExists``
    when the box is already there and ``KeyStoreRefused`` when it would take
    over the salt of the member's sealed files. Writes a ``CryptoAuditLog``
    row naming the box and nothing else; the host's chain record is the
    caller's (it knows the request).
    """
    from .crypto import GervazyCryptoSession
    from .models import UserStrongbox

    if not passphrase or len(passphrase) < PASSPHRASE_MIN_LENGTH:
        raise ValueError(f"A key store passphrase must be at least "
                         f"{PASSPHRASE_MIN_LENGTH} characters.")
    if personal_strongbox(user) is not None:
        raise KeyStoreExists("This account already has its key store.")
    try:
        with transaction.atomic():
            first_before = UserStrongbox.objects.filter(owner=user).first()
            session, _wrapped = GervazyCryptoSession.initialize_strongbox(
                user, PERSONAL_STRONGBOX_NAME, passphrase)
            session.close()
            box = session._strongbox
            first_after = UserStrongbox.objects.filter(owner=user).first()
            if first_before is not None and first_after.pk != first_before.pk:
                raise KeyStoreRefused(
                    "A new key store would change the one your sealed files use.")
    except IntegrityError as exc:
        # The unique owner+name constraint: a second request got here first.
        raise KeyStoreExists("This account already has its key store.") from exc
    _crypto_log(user, box, request)
    return box


def _crypto_log(user, box, request) -> None:
    """Gervazy's own trail: which box, by whom, from where — no secret."""
    try:
        from .models import CryptoAuditLog

        address = None
        agent = ""
        if request is not None:
            from toto.core.client_ip import client_ip

            address = client_ip(request) or None
            agent = (request.META.get("HTTP_USER_AGENT") or "")[:500]
        CryptoAuditLog.objects.create(
            actor=user, strongbox=box, action="create_personal_strongbox",
            object_type="UserStrongbox", object_id=str(box.pk), success=True,
            key_version=1, ip_address=address, user_agent=agent)
    except Exception:  # noqa: BLE001 - the trail never undoes a made box
        return
