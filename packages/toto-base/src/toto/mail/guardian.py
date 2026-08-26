"""The platform's one mailbox, and what it takes to send from it.

The platform speaks with a single voice, and which account that is must not be
a setting somebody edits and forgets.

**This used to be an OFFICE.** The "Mail Guardian" was a ``socialhub.Station``:
a post held by a person, vacant when nobody held it, listed on the public
roster. Access followed the office, so a handover changed who could read the
mailbox without touching the row or the credential, and a vacancy was loud —
:func:`platform_sender` refused to send at all rather than fall back to some
other identity.

Stations were removed in 8/2026 and the office went with them. What remains is
the property that actually protected anybody: **the platform never guesses**.
There is one system mailbox, it must be designated, it must be able to send,
and if it is not then :func:`platform_sender` raises and the send is recorded as
refused. Account recovery breaking visibly still beats account recovery
succeeding from an address nobody chose.

What is lost is the named holder — who governs the mailbox is a staff question
now, answered by the admin that designates it, rather than a rosterable
appointment. ``is_guardian`` is gone with the office; nothing outside its own
tests ever called it.
"""
from __future__ import annotations

from django.utils.translation import gettext as _


class NoSystemMailbox(RuntimeError):
    """There is no designated mailbox to send platform mail from."""


def system_mailbox():
    """The one system mailbox, or None."""
    from .models import Mailbox, MailboxKind

    return Mailbox.objects.filter(kind=MailboxKind.SYSTEM).first()


def platform_sender():
    """The mailbox platform mail must go out from. Raises, never guesses."""
    mailbox = system_mailbox()
    if mailbox is None:
        raise NoSystemMailbox(_(
            "The platform has no system mailbox. A superuser designates one "
            "from the Mail Guardian's connected accounts; until then the "
            "platform cannot send mail."))
    if not mailbox.can_send:
        raise NoSystemMailbox(_(
            "The system mailbox '%(label)s' is not ready to send: it needs a "
            "server and a stored password.") % {"label": mailbox.label})
    return mailbox


def describe() -> str:
    """One sentence for the staff console, whatever the state."""
    try:
        platform_sender()
    except NoSystemMailbox as exc:
        return str(exc)
    return _("Platform mail goes out from %(address)s.") % {
        "address": system_mailbox().email_address}
