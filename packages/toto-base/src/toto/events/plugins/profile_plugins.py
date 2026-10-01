from django.db.models import Q
from django.utils.timezone import now

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="upcoming_events",
    title="Upcoming Events",
    order=35,
)
class UpcomingEventsProfilePlugin(ProfilePlugin):
    template_name = "events/profile_plugins/upcoming_events.html"
    section_icon = "fa-solid fa-calendar-days"

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
