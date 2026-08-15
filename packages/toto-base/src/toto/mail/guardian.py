"""The Mail Guardian: the office that governs the platform's one mailbox.

The platform speaks with a single voice. Which account that is must not be a
setting somebody edits and forgets — it is an OFFICE, held by a person,
vacant when nobody holds it, and visible on the public roster like every
other Special Role (``socialhub.Station``).

Two consequences follow, and both are deliberate:

* **Access follows the office.** The mailbox row belongs to the platform, so
  a handover changes who may read it without touching the row, the
  credential, or a single line of configuration.
* **A vacancy is loud.** With no Guardian, or a Guardian who has not
  connected the mailbox, platform mail does not quietly fall back to some
  other identity — :func:`platform_sender` raises and the send is recorded
  as refused. Account recovery breaking visibly beats account recovery
  succeeding from an address nobody governs.
"""
from __future__ import annotations

from django.utils.translation import gettext as _

#: The office's slug. Seeded by ``ingress_mail``; renaming the office in the
#: admin does not move it, because the slug is what code asks for.
GUARDIAN_SLUG = "mail-guardian"
GUARDIAN_NAME = "Mail Guardian"


class NoSystemMailbox(RuntimeError):
    """There is no governed mailbox to send platform mail from."""


def guardian_station():
    """The office row, or None where socialhub is absent or unseeded."""
    try:
        from toto.socialhub.models import Station
    except Exception:  # noqa: BLE001 - socialhub is core, but stay honest
        return None
    return Station.objects.filter(slug=GUARDIAN_SLUG, active=True).first()


def current_guardian():
    """The Person holding the office, or None when it is vacant."""
    station = guardian_station()
    return station.holder if (station and station.holder_id) else None


def is_guardian(user) -> bool:
    """Whether this login currently holds the office.

    Superusers are NOT folded in here. Reading the platform's replies is the
    office's job, and quietly widening it to every superuser would make the
    answer to "who saw this" depend on a permission flag rather than on a
    named, rosterable appointment.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    holder = current_guardian()
    return bool(holder and holder.user_id == user.pk)


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
    if current_guardian() is None:
        raise NoSystemMailbox(_(
            "The Mail Guardian office is vacant, so nobody governs the "
            "platform's mailbox. Appoint a holder before the platform sends "
            "mail in its own name."))
    return mailbox


def describe() -> str:
    """One sentence for the staff console, whatever the state."""
    try:
        platform_sender()
    except NoSystemMailbox as exc:
        return str(exc)
    holder = current_guardian()
    return _("Platform mail goes out from %(address)s, governed by "
             "%(who)s.") % {"address": system_mailbox().email_address,
                            "who": holder.display_name}
