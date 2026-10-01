"""Changing one's own e-mail address, confirmed from the new address
(My account, 2026-09-30).

    request_change(user, new_email, *, request) -> bool   # the link was mailed
    confirm_change(user, token, *, request) -> str        # one of OUTCOMES

The member types a new address on My account; nothing about the account
changes yet. A random token is mailed to the NEW address through
``toto.core.notices.send_notice`` (kind ``email_change_confirm``) and only its
SHA-256 is kept, on a ``PendingEmailChange`` row — one per member, a new
request replacing the old. Opening the link, signed in as that same member,
within ``LINK_HOURS``, moves the account: ``User.email``, and ``Person.email``
with it when the Person carried the old address (or none). The row is deleted
as it is used, so the link works once; the OLD address is told
(``email_changed``). Both steps are ``AUTH.*`` records with the addresses
masked — the auth trail, where the member's Recent sign-ins read them.

**Why the member must be signed in.** The link proves the new mailbox; the
session proves the account. Either alone is not enough: a link forwarded to
someone else, or opened by another member signed in on a shared computer,
changes nothing and does not spend the link.

**Why "taken" looks at three tables.** ``User.email`` is what the social
sign-in matches a provider account onto (``social_login``, ``email__iexact``)
and what password reset mails; ``Person.email`` is what imports and the
directory match on; and an accepted ``MembershipApplication`` activates and
enrols the account whose ``User.email`` is the application's address
(``ReferenceRequest.save``) — so moving onto a pending applicant's address
would hand their acceptance to this account. An address any of them holds for
someone else is refused, compared without case, at the request AND again at
the click.

**Review, 2026-10-01.** The link is bound to the address the account had
when it was asked for: if that has changed since (an administrator moved the
account back, say), the link is spent and changes nothing. A confirmed change
ends every OTHER session of the member, as a password change does — whoever
else is signed in could otherwise watch the account move away and keep it.
Asking is rate-limited (``throttled``): per member, so no one makes the
platform mail an address over and over, and per address, so several accounts
together cannot either.

A federated account (no usable password here) takes its address from its
provider at every sign-in (``sso_client``); a local change would be undone,
so the page says to change it there, as for the password.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from toto.core.client_ip import client_ip
from toto.core.notices import send_notice

log = logging.getLogger("toto.socialhub")

#: How long the mailed link works.
LINK_HOURS = 24

#: Requests a member may make, and mails one address may be sent, per window
#: (``toto.core.ratelimit``, 2026-10-01). A refused form counts too: the
#: "taken" answer says whether an address is someone's, and that is not to be
#: asked a thousand times.
REQUESTS_PER_MEMBER = 5
MEMBER_WINDOW = 3600
MAILS_PER_ADDRESS = 3
ADDRESS_WINDOW = 24 * 3600

#: What ``confirm_change`` answers.
CHANGED = "changed"
INVALID = "invalid"  # unknown, already used, or replaced by a newer request
EXPIRED = "expired"
NOT_YOURS = "not_yours"  # a real link, opened by another signed-in member
TAKEN = "taken"  # the address went to someone else since the request
OUTCOMES = (CHANGED, INVALID, EXPIRED, NOT_YOURS, TAKEN)


def mask_email(address: str) -> str:
    """``jane@example.org`` -> ``j***@example.org``: enough to recognise, not to write to."""
    local, _, domain = (address or "").partition("@")
    if not domain:
        return "***"
    return f"{local[:1]}***@{domain}"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def address_taken(address: str, user) -> bool:
    """Whether ``address`` is some OTHER account's, person's or applicant's."""
    from django.contrib.auth import get_user_model

    from toto.people.models import Person
    from toto.socialhub.models import MembershipApplication

    address = (address or "").strip()
    if not address:
        return False
    if get_user_model().objects.filter(email__iexact=address).exclude(pk=user.pk).exists():
        return True
    if Person.objects.filter(email__iexact=address).exclude(user_id=user.pk).exists():
        return True
    return MembershipApplication.objects.filter(email__iexact=address).exists()


def throttled(user, address: str = "") -> int:
    """Count one request; the seconds to wait when over a limit, else 0.

    Without ``address`` it counts the member's asking (every POST, before the
    form is read); with one, a mail about to go to that address.
    """
    from toto.core import ratelimit

    if address:
        tag = hashlib.sha256(address.strip().lower().encode()).hexdigest()[:32]
        hit = ratelimit.hit(f"account:email:to:{tag}", limit=MAILS_PER_ADDRESS,
                            window=ADDRESS_WINDOW)
    else:
        hit = ratelimit.hit(f"account:email:by:{user.pk}", limit=REQUESTS_PER_MEMBER,
                            window=MEMBER_WINDOW)
    return 0 if hit.allowed else hit.retry_after


def _on_chain(name, user, request, **values):
    from django.apps import apps

    if not apps.is_installed("toto.audit"):
        return None
    from toto.audit import identity

    return getattr(identity, name)(user, request=request, **values)


def request_change(user, new_email: str, *, request) -> bool:
    """Mail the confirmation link to ``new_email``; True when it is on its way
    (``send_notice``: queued for the worker, or taken by the backend).

    The caller has validated the address (``AccountEmailForm``). Whatever the
    mail's fate, the request is on the chain and the row waits: a member whose
    mail did not leave can ask again, which replaces it.
    """
    from toto.socialhub.models import PendingEmailChange

    new_email = new_email.strip()
    token = secrets.token_urlsafe(32)
    with transaction.atomic():
        PendingEmailChange.objects.filter(user=user).delete()
        PendingEmailChange.objects.create(user=user, new_email=new_email,
                                          old_email=(user.email or "").strip(),
                                          token_hash=_hash(token))
    link = (request.build_absolute_uri(reverse("account:email_confirm"))
            + "?" + urlencode({"token": token}))
    _on_chain("on_email_change_requested", user, request, new_email=mask_email(new_email))
    return send_notice(user, "email_change_confirm",
                       {"link": link, "new_email": new_email, "hours": LINK_HOURS},
                       to=new_email)


def pending_for(user):
    """The member's waiting request, or None — expired ones are not waiting."""
    from toto.socialhub.models import PendingEmailChange

    row = PendingEmailChange.objects.filter(user=user).first()
    if row is None or row.created < timezone.now() - timedelta(hours=LINK_HOURS):
        return None
    return row


def _sync_person(user, old: str, new: str) -> None:
    person = getattr(user, "community_profile", None)
    if person is None or not person.pk:
        return
    # Person.email is copied from the account when the Person is made and
    # nothing kept them together since; follow the account when the Person
    # still says what the account said. A different address there was set on
    # purpose by someone and is not this page's to overwrite.
    current = (person.email or "").strip()
    if not current or current.lower() == (old or "").lower():
        person.email = new
        person.save(update_fields=["email"])


def confirm_change(user, token: str, *, request) -> str:
    """Apply the change ``token`` names, for ``user`` only. See the module docstring."""
    from django.contrib.auth import get_user_model

    from toto.socialhub.models import PendingEmailChange

    if not token or len(token) > 200:
        return INVALID
    with transaction.atomic():
        row = (PendingEmailChange.objects.select_for_update()
               .filter(token_hash=_hash(token)).first())
        if row is None:
            return INVALID
        if row.user_id != user.pk:
            # Not spent: the member it belongs to can still open it.
            log.info("email change: account %s opened account %s's link", user.pk, row.user_id)
            return NOT_YOURS
        if row.created < timezone.now() - timedelta(hours=LINK_HOURS):
            row.delete()
            return EXPIRED
        new = row.new_email
        if address_taken(new, user):
            row.delete()
            return TAKEN
        account = get_user_model().objects.select_for_update().get(pk=user.pk)
        old = (account.email or "").strip()
        if old.lower() != (row.old_email or "").strip().lower():
            # The account's address moved since the link was asked for: the
            # link was about an address the account no longer has.
            row.delete()
            return INVALID
        account.email = new
        account.save(update_fields=["email"])
        user.email = new
        _sync_person(user, old, new)
        row.delete()
    from toto.core.user_sessions import end_other_sessions

    session = getattr(request, "session", None)
    ended = end_other_sessions(user, keep=getattr(session, "session_key", None) or "")
    _on_chain("on_email_changed", user, request,
              old_email=mask_email(old), new_email=mask_email(new), sessions_ended=ended)
    if old and old.lower() != new.lower():
        send_notice(user, "email_changed",
                    {"new_masked": mask_email(new), "address": client_ip(request)}, to=old)
    return CHANGED
