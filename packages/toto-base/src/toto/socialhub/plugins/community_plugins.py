from typing import Any, ClassVar

from django.db.models import Q

from toto.core.plugin import BasePlugin
from toto.socialhub.models import CommunityNewsPost
from toto.socialhub.permissions import can_manage_community_news


class CommunityPlugin(BasePlugin):
    """
    Base class for plugins rendered on SocialHub community detail pages.
    """

    registry: ClassVar[dict[str, "CommunityPlugin"]] = {}

    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"

    @staticmethod
    def get_community_from_kwargs(**kwargs):
        return kwargs.get("community")

    def is_visible(self, **kwargs) -> bool:
        if not super().is_visible(**kwargs):
            return False
        return self.get_community_from_kwargs(**kwargs) is not None

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        context.update({
            "community_plugin": self,
            "community_plugin_key": self.get_key(),
            "community_plugin_title": self.get_title(),
            "community_plugin_icon": self.section_icon,
        })
        return context


@CommunityPlugin.plugin(key="community_news", title="Community News", order=20)
class CommunityNewsPlugin(CommunityPlugin):
    section_icon = "fa-solid fa-bullhorn"
    template_name = "socialhub/community_plugins/news.html"

    def get_context(self, **kwargs) -> dict[str, Any]:
        context = super().get_context(**kwargs)
        community = kwargs["community"]
        request = kwargs.get("request")
        posts = (
            CommunityNewsPost.objects
            .filter(community=community)
            .select_related("author", "community")
            .prefetch_related("topics")[:8]
        )
        context.update({
            "community_news": posts,
            "can_manage_news": can_manage_community_news(request, community) if request else False,
        })
        return context


@CommunityPlugin.plugin(key="community_calendar", title="Calendar", order=30)
class CommunityCalendarPlugin(CommunityPlugin):
    """What this community's people have scheduled.

    WHOSE EVENTS ARE "THE COMMUNITY'S"? There is no FK from `ScheduledEvent`
    to `Community` and this plugin does not add one: `toto.events` is Core and
    knows nothing about communities, and a column on the Core model would
    outlive whatever gave it meaning — the same argument `LodgeEvent` and
    `CompanyEvent` make for being join rows rather than columns.

    So the answer is derived: events OWNED OR ORGANISED by a member of this
    community. That is a real relationship already recorded in the data, it
    needs no migration, and it cannot go stale — somebody who leaves the
    community stops contributing events to it the moment their membership
    ends.

    FILTERED THROUGH `visible_events` FIRST, and that is not optional. A
    community page is readable by people who are not in the community; without
    the filter this section would list every private event any member owns,
    to anyone who can open the page.
    """

    section_icon = "fa-solid fa-calendar-days"
    template_name = "socialhub/community_plugins/calendar.html"

    def get_context(self, **kwargs) -> dict[str, Any]:
        from toto.events.access import visible_events
        from toto.events.calendar import calendar_colors, calendar_payload

        context = super().get_context(**kwargs)
        community = kwargs["community"]
        request = kwargs.get("request")
        user = getattr(request, "user", None)

        members = list(
            community.members.values_list("pk", flat=True))
        head_pk = getattr(community, "head_id", None)
        if head_pk:
            members.append(head_pk)

        events = (visible_events(user)
                  .filter(Q(owner__in=members) | Q(organizers__in=members))
                  .order_by("start_time").distinct()[:100])
        context.update({
            "community_events": events,
            "calendar_data": calendar_payload(events),
            "calendar_colors": calendar_colors(context),
        })
        return context
