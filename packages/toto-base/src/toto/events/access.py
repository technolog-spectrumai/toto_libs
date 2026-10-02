"""Which events a user may see — and who organises one, whose availability
they see and when with its reasons. One rule each, in one place.

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


def _person(user):
    if not user or not getattr(user, "is_authenticated", False):
        return None
    return getattr(user, "community_profile", None)


def may_organise(user, event) -> bool:
    """Whether ``user`` organises ``event`` — invites people to it and plans
    it: its owner or one of its organisers, and nobody else.

    NO STAFF OR SUPERUSER ARM (2026-10-02, crown 41). Every staff account and
    every superuser, plan or none, counted as an organiser of every event: on
    the planning page and the desktop API each invited anybody to anything
    and read every invitee's availability with the reason typed. The platform
    team manages events in the admin. One rule for the pages and the API
    (``api_views.EventInviteApiView`` had its own copy of the old one)."""
    person = _person(user)
    if person is None or event is None:
        return False
    return event.owner_id == person.pk or event.organizers.filter(pk=person.pk).exists()


def may_see_availability(user, person, event) -> bool:
    """Whether ``user`` may see ``person``'s availability PERIODS against
    ``event`` — their kind and times, the reason aside
    (:func:`may_see_availability_reasons`): the person themself, and an
    organiser of the event (:func:`may_organise`) when the person is
    INVITED to it, whatever they answered.

    The invitation is new (2026-10-02, crown 41): an organiser saw anybody's,
    so any member who made an event — one spanning a year overlaps every
    period — read every other member's reasons through it."""
    if person is None or event is None:
        return False
    if _is_self(user, person):
        return True
    return may_organise(user, event) and event.invites.filter(person=person).exists()


def may_see_availability_reasons(user, person, event) -> bool:
    """Whether ``user`` also sees the REASON ``person`` typed on each period
    that overlaps ``event``: the person themself, and an organiser of the
    event once the person has ACCEPTED the invitation — a pending or
    declined one shows the periods without it.

    The owner's decision of 2026-10-02 ("reasons only after accepting"),
    closing what crown 41's invitation rule left open: any member could
    make a year-long event, invite somebody and read every reason they had
    typed for that year without their saying yes to anything. No staff or
    superuser arm, as in :func:`may_organise`."""
    from .models import EventInvite

    if person is None or event is None:
        return False
    if _is_self(user, person):
        return True
    return may_organise(user, event) and event.invites.filter(
        person=person, status=EventInvite.Status.ACCEPTED).exists()


def event_periods(person, event):
    """``person``'s availability periods that overlap ``event``'s own times
    — the only ones anybody is shown against it, whatever window a page or
    a caller has in mind. Touching edges do not overlap."""
    return person.availabilities.filter(
        start_time__lt=event.end_time, end_time__gt=event.start_time,
    ).order_by("start_time")


def _is_self(user, person) -> bool:
    own = _person(user)
    return own is not None and own.pk == person.pk
