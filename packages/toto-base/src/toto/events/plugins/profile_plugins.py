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
        current_time = now()

        from toto.events.models import EventInvite, ScheduledEvent

        organizing = (
            ScheduledEvent.objects
            .filter(
                Q(owner=profile) | Q(organizers=profile),
                start_time__gte=current_time,
                public=True,
            )
            .distinct()
            .order_by("start_time")[:5]
        )

        attending = (
            ScheduledEvent.objects
            .filter(
                invites__person=profile,
                invites__status=EventInvite.Status.ACCEPTED,
                start_time__gte=current_time,
            )
            .exclude(Q(owner=profile) | Q(organizers=profile))
            .order_by("start_time")[:5]
        )

        context["organizing_events"] = organizing
        context["attending_events"] = attending
        context["has_any"] = organizing.exists() or attending.exists()
        return context
