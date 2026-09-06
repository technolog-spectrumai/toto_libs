"""Which events a user may see. One rule, in one place.

It was written inside `EventCalendarView.get_queryset` and lived only there,
which is how two other doors ended up answering a different question:
`EventDetailView` had no `get_queryset` at all, and `EventListApiView` was a
bare `.all()`. Both handed any signed-in caller every private event on the
platform — the exact thing the calendar's filter had been added to stop, and
which `toto.tax.notices` still writes deliberately neutral titles to work
around.

So the rule moved here and the views ask it. The same shape `toto.vault`'s
`access.may_read` and `toto.lacedo`'s `visible_bounties` use: a queryset
function beside a per-object twin, so a list page and a detail page cannot
drift apart.
"""

from __future__ import annotations

from django.db.models import Q


def visible_events(user, queryset=None):
    """Public events, plus the ones this person is actually part of.

    ANONYMOUS GETS THE PUBLIC ARM ONLY. An unauthenticated request has no
    ownership, no organiser role and no invitation to check.

    The default of `public` is still True, so nothing meant to be seen
    disappears; only an event somebody explicitly marked private starts
    behaving like one.
    """
    from .models import ScheduledEvent

    qs = ScheduledEvent.objects.all() if queryset is None else queryset
    if not user or not getattr(user, "is_authenticated", False):
        return qs.filter(public=True)

    person = getattr(user, "community_profile", None)
    if person is None:
        # A login with no Person cannot own, organise or be invited to
        # anything, so there is nothing private for them to see.
        return qs.filter(public=True)

    return qs.filter(
        Q(public=True)
        | Q(owner=person)
        | Q(organizers=person)
        | Q(invites__person=person)
    ).distinct()


def may_read(user, event) -> bool:
    """The per-object twin of :func:`visible_events`.

    Expressed THROUGH the queryset rather than as a parallel set of clauses,
    so the two can never disagree — the failure this module exists to end.
    """
    if event is None:
        return False
    return visible_events(user).filter(pk=event.pk).exists()
