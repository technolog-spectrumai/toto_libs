"""Narrow reads of the chain for a page that is not the audit pages (2026-09-30).

The audit pages are staff-only and stay so. A member may still see what the
chain says about their own account — My account's "Recent sign-ins" — and
reads it here, so the rule of which rows are theirs lives with the chain and
not in a view:

    member_auth_records(user, *, days=30) -> QuerySet[AuditRecord]
    records_about(user, *, also=()) -> QuerySet[AuditRecord]

Theirs means an ``AUTH.*`` record whose actor is the member or whose subject
is the member's account (``object_type`` ``auth.user``, ``object_id`` their
id). A refused sign-in and a pause have their subject too since 2026-10-02:
the one account the typed name was a try at, decided as they are written
(``identity._account_named``). They used to be matched here by the name
typed — the member's username or e-mail address, any case — which gave one
record to every account it matched: an account whose username was another's
address saw that member's failed sign-ins, address and browser included, and
they its. A pause of a whole address names no one and is not theirs.

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


def member_auth_records(user, *, days: int = 30):
    """The member's own ``AUTH.*`` records of the last ``days`` days, newest first."""
    if not getattr(user, "pk", None):
        return AuditRecord.objects.none()
    since = timezone.now() - timedelta(days=days)
    theirs = Q(actor_user_id=user.pk) | Q(object_type="auth.user", object_id=str(user.pk))
    return (AuditRecord.objects
            .filter(action__startswith="AUTH.", timestamp__gte=since)
            .filter(theirs)
            .order_by("-timestamp", "-sequence"))


def records_about(user, *, also=()):
    """The records whose subject is ``user`` and whose actor is somebody
    else, oldest first, every one the chain has (2026-10-01).

    Their subject is the member when the record is about their account
    (``auth.user`` and their id — an administrator granting staff, the
    acceptance that activated them, a refused token, a console export, a
    failed sign-in or a pause at their account), or is one of the
    socialhub's records that carry their account's id in ``metadata["user"]``
    — a community or clearance given or taken, a senior named, their data
    copy filed, their erasure request declined. ``also``
    adds subjects the caller knows are theirs and the chain cannot tell, as
    ``(object_type, ids)`` pairs: their profile, the membership applications
    made with their address and the references asked for them.

    Each row still holds what the other side's request carried
    (``request_source``: address and browser); the caller leaves that out.
    """
    if not getattr(user, "pk", None):
        return AuditRecord.objects.none()
    about = (Q(object_type="auth.user", object_id=str(user.pk))
             | Q(app_label="socialhub", metadata__user=user.pk))
    for object_type, ids in also:
        ids = [str(pk) for pk in ids]
        if ids:
            about |= Q(object_type=object_type, object_id__in=ids)
    return (AuditRecord.objects.filter(about)
            .exclude(actor_user_id=user.pk)
            .order_by("timestamp", "sequence"))
