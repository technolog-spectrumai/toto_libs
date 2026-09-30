"""Narrow reads of the chain for a page that is not the audit pages (2026-09-30).

The audit pages are staff-only and stay so. A member may still see what the
chain says about their own account — My account's "Recent sign-ins" — and
reads it here, so the rule of which rows are theirs lives with the chain and
not in a view:

    member_auth_records(user, *, days=30) -> QuerySet[AuditRecord]

Theirs means an ``AUTH.*`` record whose actor is the member, whose subject is
the member's account (``object_type`` ``auth.user``, ``object_id`` their id),
or a refused sign-in or a pause that names them by the name that was typed —
their username or their e-mail address, any case. A refused sign-in has no
account behind it (``identity.on_login_failed`` records the typed name only),
so that is the only way to find the guesses made against a member; a pause
of a whole address names no one and is not theirs.
"""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from toto.audit.models import AuditRecord

#: The records that name an account only by the name someone typed.
TYPED_NAME_ACTIONS = ("AUTH.LOGIN_FAILED", "AUTH.LOCKED")


def member_auth_records(user, *, days: int = 30):
    """The member's own ``AUTH.*`` records of the last ``days`` days, newest first."""
    if not getattr(user, "pk", None):
        return AuditRecord.objects.none()
    since = timezone.now() - timedelta(days=days)
    theirs = (Q(actor_user_id=user.pk)
              | Q(object_type="auth.user", object_id=str(user.pk)))
    typed = Q(object_description__iexact=user.get_username())
    email = (getattr(user, "email", "") or "").strip()
    if email:
        typed |= Q(object_description__iexact=email)
    # object_id is empty on these: the name was typed, not looked up — the
    # guard that keeps a row about ANOTHER account (object_id set) out even
    # if its description happens to match.
    theirs |= Q(action__in=TYPED_NAME_ACTIONS, object_id="") & typed
    return (AuditRecord.objects
            .filter(action__startswith="AUTH.", timestamp__gte=since)
            .filter(theirs)
            .order_by("-timestamp", "-sequence"))
