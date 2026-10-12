"""How a person gets an account on this platform, and what they end up with
(2026-10-10).

Two ways in, and each platform decides which is its own
(``settings.SOCIALHUB_ENROLLMENT``):

``"application"`` (the default)
    The patron's way: a person applies to a community, proves they are a
    person, names a member as their referrer; the referrer admits them
    (``views/application.py``).

``"invitation"``
    The team's way: somebody on staff issues a single-use link for a
    community, mailed to the address named or handed over; whoever opens it
    makes their own account (``invitations.py``).

The end is the same either way, and it is ONE function, :func:`admit`: an
active account, a ``Person``, a member of the community, the member who
vouched as the patron, and the privacy notice they accepted carried to the
person.

The other way's doors are closed on a platform (404), not hidden. An account
made at the console (``create_user``) is open to both.
"""
from __future__ import annotations

from django.conf import settings
from django.core.checks import Error, register
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.utils import timezone

APPLICATION = "application"
INVITATION = "invitation"
WAYS = (APPLICATION, INVITATION)


def way() -> str:
    """This platform's way in. Unset: applications, as before there were two."""
    value = str(getattr(settings, "SOCIALHUB_ENROLLMENT", APPLICATION) or APPLICATION).strip().lower()
    if value not in WAYS:
        raise ImproperlyConfigured(
            f"SOCIALHUB_ENROLLMENT is {value!r}; it is one of {', '.join(WAYS)}.")
    return value


def by_application() -> bool:
    return way() == APPLICATION


def by_invitation() -> bool:
    return way() == INVITATION


def application_only(view):
    """A door of the application's way: 404 on a platform that invites."""
    from functools import wraps

    @wraps(view)
    def door(request, *args, **kwargs):
        if not by_application():
            raise Http404("This platform does not take membership applications.")
        return view(request, *args, **kwargs)

    return door


def invitation_only(view):
    """A door of the invitation's way: 404 on a platform that takes applications."""
    from functools import wraps

    @wraps(view)
    def door(request, *args, **kwargs):
        if not by_invitation():
            raise Http404("This platform does not issue invitations.")
        return view(request, *args, **kwargs)

    return door


@register()
def check_enrollment(app_configs, **kwargs):
    try:
        way()
    except ImproperlyConfigured as exc:
        return [Error(str(exc), id="socialhub.E001")]
    return []


def admit(user, community, *, patron=None, display_name="", privacy_version=None,
          privacy_accepted_at=None):
    """Let ``user`` in: what an accepted application and an accepted
    invitation both end in. Returns the ``Person``.

    1. The account is active.
    2. It has a ``Person`` (made with ``display_name``, else the username,
       and the account's address; one that exists is kept as it is).
    3. The person is a member of ``community``.
    4. ``patron`` (the member who vouched: the referrer, the inviter) becomes
       their patron, unless one is already set or it is themselves. An
       inviter with no ``Person`` (a staff account made at the console)
       vouches for nobody: the member has no patron.
    5. The privacy notice version they accepted goes with the person: an
       application or an invitation is housekeeping's to prune, the
       acceptance has to outlive it.
    """
    from toto.people.models import Person

    from .models import PrivacyAcceptance

    if not user.is_active:
        user.is_active = True
        user.save(update_fields=["is_active"])
    member, _created = Person.objects.get_or_create(
        user=user, defaults={"display_name": display_name or user.username,
                             "email": user.email})
    member.communities.add(community)
    if patron is not None and member.patron_id is None and patron.pk != member.pk:
        member.patron = patron
    member.save()
    if privacy_version:
        PrivacyAcceptance.objects.get_or_create(
            person=member, version=privacy_version,
            defaults={"accepted_at": privacy_accepted_at or timezone.now()})
    return member
