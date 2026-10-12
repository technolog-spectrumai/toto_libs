"""Invitations: the way in of a platform that invites (2026-10-10).

``settings.SOCIALHUB_ENROLLMENT = "invitation"`` (``enrollment.py``). Staff
issue a link for a community on the socialhub's Invitations tab: shown once,
mailed when an address was named, good for ``SOCIALHUB_INVITATION_HOURS``
(72). Whoever opens it before then chooses a username and a password, reads
and accepts the privacy notice, and is admitted exactly as an accepted
application is (``enrollment.admit``): a person in that community, with the
member who issued the link as their patron (none when the issuer has no
profile, or their account is gone by then). The link is spent. A link stops
working when the account that issued it is no longer active staff.

The token is in the link only: the row keeps its SHA-256. An invitation
(used, expired or revoked alike) is deleted by the nightly housekeeping
``SOCIALHUB_EXPIRED_INVITATION_DAYS`` (30) days after its link expired; the
person's privacy acceptance and community outlive it. A member's invitations
(the one their account was made with, any made out to their address) are in
their data copy and go with their account.
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMessage
from django.db import models
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from . import enrollment
from .models import Invitation, _token_hash

log = logging.getLogger("toto.socialhub")

#: Hours a link works when the host names none.
DEFAULT_HOURS = 72
#: Days after its link expired that an invitation is deleted, when the host
#: sets no ``SOCIALHUB_EXPIRED_INVITATION_DAYS``; 0 keeps them for good.
DEFAULT_EXPIRED_DAYS = 30


def hours() -> int:
    return max(1, int(getattr(settings, "SOCIALHUB_INVITATION_HOURS", DEFAULT_HOURS)))


def expired_days() -> int:
    return max(0, int(getattr(settings, "SOCIALHUB_EXPIRED_INVITATION_DAYS",
                              DEFAULT_EXPIRED_DAYS)))


def may_issue(user) -> bool:
    """Who issues and revokes invitations: active staff, on a platform that
    invites."""
    return bool(enrollment.by_invitation() and getattr(user, "is_authenticated", False)
                and user.is_active and (user.is_staff or user.is_superuser))


def issue(*, issued_by, community, email="", note=""):
    """A new invitation and the token it answers to, which is shown once."""
    token = secrets.token_urlsafe(32)
    row = Invitation.objects.create(
        token_hash=_token_hash(token), email=(email or "").strip(), community=community,
        note=note, issued_by=issued_by, expires_at=timezone.now() + timedelta(hours=hours()))
    return row, token


def of_member(user):
    """The invitations that are this member's: the one their account
    accepted, and any made out to their account's or profile's address
    (case-insensitive) that NOBODY accepted. An accepted invitation is the
    accepting account's alone, whatever address it carries: two people can
    hold one address over time, and the row has a note about one of them.
    What the data copy lists and the erase deletes."""
    q = models.Q(accepted_user=user)
    addresses = {(user.email or "").strip().lower()}
    person = getattr(user, "community_profile", None)
    if person is not None:
        addresses.add((person.email or "").strip().lower())
    for address in sorted(addresses - {""}):
        q |= models.Q(email__iexact=address, accepted_at__isnull=True)
    return Invitation.objects.filter(q)


def address_held(address) -> bool:
    """Whether an account has ``address`` (as its e-mail or as its username)
    or a person has it: an invitation makes no second account at it."""
    from toto.people.models import Person

    address = (address or "").strip()
    if not address:
        return False
    return (get_user_model().objects.filter(
                models.Q(email__iexact=address) | models.Q(username__iexact=address)).exists()
            or Person.objects.filter(email__iexact=address).exists())


def made_out_to(address, *, but=None) -> bool:
    """Whether an invitation nobody accepted is made out to ``address``
    (``but`` aside): the address is that invitee's to claim, not a stranger's
    with another link. Expired and revoked ones count: the row still carries
    a note about that person, and ``of_member`` goes by the address."""
    rows = Invitation.objects.filter(email__iexact=(address or "").strip(),
                                     accepted_at__isnull=True)
    if but is not None:
        rows = rows.exclude(pk=but.pk)
    return rows.exists()


def mail(invitation, link) -> bool:
    """Mail the link to the invitation's address, in the platform's language
    (the invitee has no account whose language could be asked). False when
    there is no address, the platform has no mail delivery set up (a console
    or dummy backend would print or drop the link: saying "mailed" would be
    false, and the token would be in a log) or the mail could not be handed
    over: the issuer then hands the link over themselves."""
    from toto.core.email_config import email_delivery_configured

    if not invitation.email or not email_delivery_configured():
        return False
    names = {"community": invitation.community.name if invitation.community else "",
             "hours": hours(), "link": link}
    with translation.override(settings.LANGUAGE_CODE):
        subject = _("An invitation to %(community)s") % names
        body = _("Hello,\n"
                 "\n"
                 "You are invited to join %(community)s.\n"
                 "\n"
                 "Open this link to make your account. It works once, for %(hours)s "
                 "hours:\n"
                 "\n"
                 "%(link)s\n"
                 "\n"
                 "If you were not expecting this, ignore this message.") % names
    try:
        EmailMessage(subject=subject, body=body, to=[invitation.email]).send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 - the link is shown to the issuer either way
        log.error("Invitation %s: the mail could not be sent: %s", invitation.pk,
                  type(exc).__name__)
        return False
    return True


def accept(invitation, *, username, display_name, email, password, privacy_version):
    """Make the account ``invitation`` is for and admit it. The caller holds
    the row locked inside a transaction and has checked it is open. Returns
    ``(user, person)``."""
    from toto.people.models import Person

    user = get_user_model().objects.create_user(username=username, email=email or "",
                                                password=password)
    patron = None
    if invitation.issued_by_id:
        patron = Person.objects.filter(user_id=invitation.issued_by_id).first()
    now = timezone.now()
    person = enrollment.admit(user, invitation.community, patron=patron,
                              display_name=display_name, privacy_version=privacy_version,
                              privacy_accepted_at=now)
    invitation.accepted_at = now
    invitation.accepted_user = user
    invitation.save(update_fields=["accepted_at", "accepted_user"])
    return user, person


def prune(*, days=None, now=None) -> dict:
    """Delete the invitations whose link expired more than ``days`` (default
    ``SOCIALHUB_EXPIRED_INVITATION_DAYS``) days ago: used, expired and revoked
    alike. Only the rows: the accounts made with them are members'. ``0``
    keeps them. The counts, for the housekeeping's record."""
    days = expired_days() if days is None else max(0, int(days))
    if not days:
        return {"pruned": 0}
    cutoff = (now or timezone.now()) - timedelta(days=days)
    pruned, _by_model = Invitation.objects.filter(expires_at__lt=cutoff).delete()
    return {"pruned": pruned}
