"""Narrow reads of the chain for a page that is not the audit pages (2026-09-30).

The audit pages are staff-only and stay so. A member may still see what the
chain says about their own account — My account's "Recent sign-ins" — and
reads it here, so the rule of which rows are theirs lives with the chain and
not in a view:

    member_auth_records(user, *, days=30) -> QuerySet[AuditRecord]
    records_about(user, *, also=()) -> QuerySet[AuditRecord]

Theirs means an ``AUTH.*`` record whose actor is the member, whose subject is
the member's account (``object_type`` ``auth.user``, ``object_id`` their id),
or a refused sign-in or a pause that names them by the name that was typed —
their username or their e-mail address, any case. A refused sign-in has no
account behind it (``identity.on_login_failed`` records the typed name only),
so that is the only way to find the guesses made against a member; a pause
of a whole address names no one and is not theirs.

:func:`records_about` is the other half of a member's data export
(``toto.core.personal_data``, 2026-10-01): the records about them that
somebody ELSE wrote — an administrator, a referrer, the platform itself,
whoever typed their name at the sign-in. The ones they wrote themselves are
``actor_user`` = them, which the export reads on its own.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from toto.audit.models import AuditRecord

#: The records that name an account only by the name someone typed.
TYPED_NAME_ACTIONS = ("AUTH.LOGIN_FAILED", "AUTH.LOCKED")


def _typed_name(user) -> Q:
    """A refused sign-in or a pause that names ``user`` by the name typed."""
    typed = Q(object_description__iexact=user.get_username())
    email = (getattr(user, "email", "") or "").strip()
    if email:
        typed |= Q(object_description__iexact=email)
    # object_id is empty on these: the name was typed, not looked up — the
    # guard that keeps a row about ANOTHER account (object_id set) out even
    # if its description happens to match.
    return Q(action__in=TYPED_NAME_ACTIONS, object_id="") & typed


def member_auth_records(user, *, days: int = 30):
    """The member's own ``AUTH.*`` records of the last ``days`` days, newest first."""
    if not getattr(user, "pk", None):
        return AuditRecord.objects.none()
    since = timezone.now() - timedelta(days=days)
    theirs = (Q(actor_user_id=user.pk)
              | Q(object_type="auth.user", object_id=str(user.pk))
              | _typed_name(user))
    return (AuditRecord.objects
            .filter(action__startswith="AUTH.", timestamp__gte=since)
            .filter(theirs)
            .order_by("-timestamp", "-sequence"))


def records_about(user, *, also=()):
    """The records whose subject is ``user`` and whose actor is somebody
    else, oldest first, every one the chain has (2026-10-01).

    Their subject is the member when the record is about their account
    (``auth.user`` and their id — an administrator granting staff, the
    acceptance that activated them, a refused token, a console export), names
    them by the name typed (a refused sign-in, a pause: :func:`_typed_name`),
    or is one of the socialhub's records that carry their account's id in
    ``metadata["user"]`` — a community or clearance given or taken, a senior
    named, their data copy filed, their erasure request declined. ``also``
    adds subjects the caller knows are theirs and the chain cannot tell, as
    ``(object_type, ids)`` pairs: their profile, the membership applications
    made with their address and the references asked for them.

    Each row still holds what the other side's request carried
    (``request_source``: address and browser); the caller leaves that out.
    """
    if not getattr(user, "pk", None):
        return AuditRecord.objects.none()
    about = (Q(object_type="auth.user", object_id=str(user.pk))
             | _typed_name(user)
             | Q(app_label="socialhub", metadata__user=user.pk))
    for object_type, ids in also:
        ids = [str(pk) for pk in ids]
        if ids:
            about |= Q(object_type=object_type, object_id__in=ids)
    return (AuditRecord.objects.filter(about)
            .exclude(actor_user_id=user.pk)
            .order_by("timestamp", "sequence"))
