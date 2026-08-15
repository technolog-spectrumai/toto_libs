"""Where a mailbox password lives, and who can open it.

Two regimes, one API. Callers say *which mailbox*, never *which strongbox*.

**Personal** — the owner's own ``gervazy.UserStrongbox``, unlocked by a
passphrase only they know. Nothing here caches that passphrase and nothing
writes it anywhere: the codebase's rule for personal boxes is that it is
typed per operation (``gervazy/views.py:88,157``, ``notarius/views.py:281``),
and the one place that keeps anything across requests keeps a boolean and a
token, never key material (``assets/wallet_pin.py:11-42``). We follow that:
:func:`unlock` hands back a live session, :mod:`toto.mail.unlock` holds it in
PROCESS memory for a short while so "unlock once when you open Mail" is true
within a worker, and a restart or another worker simply asks again.

**Platform** — jess's ``jess-system`` strongbox, opened from the server-side
passphrase. This is what lets the system mailbox send with nobody signed in,
and it is the reason designating a mailbox as the system one is a superuser
act with its consequence spelled out on screen.

Every failure is :class:`MailboxLocked`, a typed error callers are expected
to *show* rather than propagate — the jess ``VaultUnavailable`` convention.
"""
from __future__ import annotations

import uuid

from django.utils.translation import gettext as _

#: The strongbox a person's mail credentials live in. One per user, made on
#: first connect; separate from any box they may already have for signing or
#: a wallet PIN, so one blast radius per secret domain (jess/vault.py:14).
PERSONAL_STRONGBOX_NAME = "mail"
PURPOSE = "mail_password"


class MailboxLocked(RuntimeError):
    """The password could not be read, and the message says why."""


def _person_strongbox(user, *, create=False, password=""):
    """This user's mail strongbox, optionally provisioning it.

    Nothing in the platform creates a personal strongbox on signup — only a
    wallet PIN and a dev seed do (``assets/wallet_pin.py:83``,
    ``gervazy/management/commands/ingress_gervazy.py:19``). So connecting a
    mailbox is where a person's mail box comes into being, with the
    passphrase they choose at that moment.
    """
    from toto.gervazy.crypto import GervazyCryptoSession
    from toto.gervazy.models import UserStrongbox

    box = UserStrongbox.objects.filter(
        owner=user, name=PERSONAL_STRONGBOX_NAME).first()
    if box is not None:
        return box, None
    if not create:
        raise MailboxLocked(_(
            "This mailbox has no strongbox yet — connect it again and choose "
            "a passphrase."))
    if not password:
        raise MailboxLocked(_("A passphrase is required to seal a mailbox."))
    session, wrapped_key = GervazyCryptoSession.initialize_strongbox(
        user, PERSONAL_STRONGBOX_NAME, password)
    return session._strongbox, (session, wrapped_key)


def open_session(mailbox, passphrase: str = ""):
    """An unlocked crypto session for this mailbox, or :class:`MailboxLocked`.

    A personal mailbox needs the owner's typed passphrase. The system mailbox
    needs none from the caller — the platform holds its own.
    """
    from .models import Keyholder

    if mailbox.keyholder == Keyholder.PLATFORM:
        from toto.jess import vault as jess_vault

        try:
            return jess_vault.open_session()
        except Exception as exc:  # noqa: BLE001 - typed for the caller
            raise MailboxLocked(_(
                "The platform strongbox is not available: %(why)s")
                % {"why": exc}) from exc

    if mailbox.owner_id is None:
        raise MailboxLocked(_("This mailbox has no owner to unlock it."))
    if not passphrase:
        raise MailboxLocked(_("Your mailbox passphrase is required."))

    from toto.gervazy.crypto import GervazyCryptoSession

    box, _new = _person_strongbox(mailbox.owner)
    return GervazyCryptoSession(box, passphrase)


def store_password(mailbox, plaintext: str, *, passphrase: str = "",
                   session=None):
    """Seal a mailbox password under whichever strongbox owns this mailbox.

    Returns the ``EncryptedSecret``. Replacing a password retires the old
    secret rather than editing it, so the audit trail keeps its shape.
    """
    from toto.gervazy.models import WrappedDataKey

    from .models import Keyholder

    if not plaintext:
        raise MailboxLocked(_("A mailbox password is required."))

    if mailbox.keyholder == Keyholder.PLATFORM:
        from toto.jess import vault as jess_vault

        secret = jess_vault.store_secret(
            plaintext,
            name=jess_vault.unique_secret_name(prefix="mail-system"),
            purpose=PURPOSE)
    else:
        box, provisioned = _person_strongbox(
            mailbox.owner, create=True, password=passphrase)
        if provisioned is not None:
            session, wrapped_key = provisioned
        else:
            from toto.gervazy.crypto import GervazyCryptoSession

            session = session or GervazyCryptoSession(box, passphrase)
            wrapped_key = (WrappedDataKey.objects
                           .filter(strongbox=box, state="active")
                           .order_by("-version").first())
            if wrapped_key is None:
                raise MailboxLocked(_(
                    "Your strongbox has no active data key. Open it in "
                    "Gervazy once to provision one."))
        # Unique per secret, never per mailbox: EncryptedSecret.name is
        # unique within a strongbox, and rotating a password writes a NEW
        # secret beside the old one (which is then retired, not edited) so
        # the audit trail keeps its shape. jess mints names the same way.
        secret = session.encrypt_secret(
            wrapped_key, plaintext,
            name=f"mail-{uuid.uuid4().hex[:12]}",
            purpose=PURPOSE)

    previous = mailbox.secret
    mailbox.secret = secret
    if mailbox.pk:
        mailbox.save(update_fields=["secret", "updated_at"])
    if previous is not None and previous.pk != secret.pk:
        previous.state = "retired"
        previous.save(update_fields=["state"])
    return secret


def read_password(mailbox, *, passphrase: str = "", session=None) -> str:
    """The mailbox password in the clear, for exactly one connection."""
    if mailbox.secret_id is None:
        raise MailboxLocked(_("This mailbox has no password stored yet."))
    session = session or open_session(mailbox, passphrase)
    try:
        return session.decrypt_secret(mailbox.secret)
    except Exception as exc:  # noqa: BLE001 - a wrong passphrase lands here
        raise MailboxLocked(_(
            "That passphrase did not open this mailbox.")) from exc
