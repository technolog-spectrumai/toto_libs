from django.db.models import Q
from django.utils.timezone import now
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="upcoming_events",
    title=_("Upcoming Events"),
    order=35,
)
class UpcomingEventsProfilePlugin(ProfilePlugin):
    template_name = "events/profile_plugins/upcoming_events.html"
    section_icon = "fa-solid fa-calendar-days"
    # On the profile's Activity tab (2026-10-02, stage 50), for the owner
    # and for visitors alike: the lists are the VIEWER's events, below.
    tab = "activity"

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        profile = kwargs["profile"]
        request = self.get_request_from_kwargs(**kwargs)
        viewer = getattr(request, "user", None)
        current_time = now()

        from toto.events.access import visible_events
        from toto.events.models import EventInvite, ScheduledEvent

        # Both lists are the VIEWER's events (2026-10-01, 37c.25): the ones
        # `access.visible_events` lets them read, the calendar's rule. The
        # attending list had no filter at all, so the title, time and place
        # of every private event an invitee had accepted showed to anybody
        # who opened that invitee's profile — the place can be someone's
        # home, and the event's owner had been told only the people it
        # names would see it.
        organizing = (
            visible_events(viewer, ScheduledEvent.objects.filter(
                Q(owner=profile) | Q(organizers=profile),
                start_time__gte=current_time,
            ))
            .distinct()
            .order_by("start_time")[:5]
        )

        attending = (
            visible_events(viewer, ScheduledEvent.objects.filter(
                invites__person=profile,
                invites__status=EventInvite.Status.ACCEPTED,
                start_time__gte=current_time,
            ))
            .exclude(Q(owner=profile) | Q(organizers=profile))
            .distinct()
            .order_by("start_time")[:5]
        )

        context["organizing_events"] = organizing
        context["attending_events"] = attending
        context["has_any"] = organizing.exists() or attending.exists()
        return context


@ProfilePlugin.plugin(
    key="event_invitations",
    title=_("Invitations to events"),
    order=16,
)
class EventInvitationsProfilePlugin(ProfilePlugin):
    """A person's own invitations to events, on their own profile
    (2026-10-12): the ones still waiting for an answer, each with Accept and
    Decline, and the answered ones of events to come. The profile's owner
    sees it and nobody else does: an invitation is theirs."""

    template_name = "events/profile_plugins/invitations.html"
    section_icon = "fa-solid fa-envelope-open-text"
    show_for_owner_only = True
    # The Overview: the page a profile opens on.
    tab = "overview"

    #: How many of each list the profile draws; the rest is the page's.
    SHOWN = 5

    def is_visible_for_profile(self, **kwargs) -> bool:
        profile = kwargs.get("profile")
        return bool(profile and getattr(profile, "user_id", None))

    def get_context(self, **kwargs):
        from django.urls import NoReverseMatch, reverse

        from toto.events.models import EventInvite

        context = super().get_context(**kwargs)
        profile = kwargs["profile"]
        request = self.get_request_from_kwargs(**kwargs)
        # Of events that are not over: an unanswered invitation to a past
        # event has nothing left to answer.
        upcoming = (EventInvite.objects.filter(person=profile, event__end_time__gte=now())
                    .select_related("event", "event__owner").order_by("event__start_time"))
        pending = upcoming.filter(status=EventInvite.Status.PENDING)
        answered = upcoming.exclude(status=EventInvite.Status.PENDING)
        try:
            all_url = reverse("events:my_invites")
        except NoReverseMatch:
            all_url = ""
        context.update({
            "pending_invites": list(pending[:self.SHOWN]),
            "pending_count": pending.count(),
            "answered_invites": list(answered[:self.SHOWN]),
            "invites_url": all_url,
            # Where an answer given here comes back to: this profile.
            "invites_next": getattr(request, "path", "") or "",
        })
        return context
