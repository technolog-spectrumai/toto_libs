from typing import Any, ClassVar

from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from toto.core.plugin import BasePlugin, RenderedPlugin


#: The address parameter that names a tab of the community page.
TAB_PARAM = "tab"


class CommunityPlugin(BasePlugin):
    """
    Base class for plugins rendered on SocialHub community detail pages.

    ``tab`` (2026-10-06, stage 65) puts a plugin's section on a tab of its
    own instead of on the page: a short name for the address
    (``?tab=<name>``), with ``tab_title`` as the strip's word for it (the
    plugin's title when empty). A plugin without one is a section of the
    Overview, as every plugin was. The strip is drawn only when a tabbed
    plugin shows for this community and this viewer (``tabs_shown``), so a
    community no tabbed plugin shows for looks as it always did; the page
    draws the active tab's plugins alone, and the others are not asked for
    anything but ``is_visible``.
    """

    registry: ClassVar[dict[str, "CommunityPlugin"]] = {}

    section_icon: ClassVar[str] = "fa-solid fa-puzzle-piece"
    tab: ClassVar[str] = ""
    tab_title: ClassVar[Any] = ""

    @classmethod
    def get_tab(cls) -> str:
        return cls.tab or ""

    @classmethod
    def on_tab(cls, tab: str) -> list["CommunityPlugin"]:
        """The plugins whose section is on ``tab`` (``""`` is the Overview),
        in their order."""
        return [plugin for plugin in cls.all() if plugin.get_tab() == (tab or "")]

    @classmethod
    def tabs_shown(cls, **kwargs) -> list[dict[str, Any]]:
        """The tabs with a plugin that shows for this request, in the order
        of their first plugin: ``[{"key", "label", "icon"}]``. Nothing is
        rendered: each plugin answers by its own ``is_visible``."""
        shown: dict[str, dict[str, Any]] = {}
        for plugin in cls.all():
            tab = plugin.get_tab()
            if not tab or tab in shown or not plugin.is_visible(**kwargs):
                continue
            shown[tab] = {"key": tab, "label": plugin.tab_title or plugin.get_title(),
                          "icon": plugin.section_icon}
        return list(shown.values())

    @classmethod
    def render_tab(cls, tab: str, **kwargs) -> list[RenderedPlugin]:
        """``render_all`` for one tab: the other tabs' plugins are not
        rendered."""
        rendered = []
        for plugin in cls.on_tab(tab):
            result = plugin.render(**kwargs)
            if result is not None:
                rendered.append(result)
        return sorted(rendered)

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


@CommunityPlugin.plugin(key="community_forum", title="Forum", order=20)
class CommunityForumPlugin(CommunityPlugin):
    """The way into the community's channel (2026-10-07).

    A community has exactly one channel in ``toto.forum``, addressed by the
    community's own slug, so there is nothing to link by hand: the row
    ``CommunityForum`` that named a room by its slug is gone. The panel is
    shown to who may read the channel (``toto.forum.access.may_read``: a
    member, a senior member or the head on a plan with the forum, or an
    administrator) and to nobody else, so it never offers a door that would
    answer 402 or 403.

    `CommunityNewsPost` and its views are still in place: the panel that
    showed them went, the posts did not.
    """

    section_icon = "fa-solid fa-comments"
    template_name = "socialhub/community_plugins/forum.html"

    def is_visible(self, **kwargs) -> bool:
        # A host without the forum shows no panel about one (2026-10-04).
        from django.apps import apps

        if not apps.is_installed("toto.forum") or not super().is_visible(**kwargs):
            return False
        from toto.forum import access

        user = getattr(kwargs.get("request"), "user", None)
        return access.may_read(user, self.get_community_from_kwargs(**kwargs))


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


@CommunityPlugin.plugin(key="community_org_chart", title=_("Organisation chart"), order=60)
class CommunityOrgChartPlugin(CommunityPlugin):
    """The community's organisation chart, on a tab of its own (2026-10-06,
    stage 66): positions, the people assigned and who reports to whom, as a
    table, for every kind of community.

    For a signed-in viewer, where the community has a position; for who may
    change the chart (``may_moderate_community``: the head, an
    administrator) also where it has none yet, with the forms. A community
    with no position shows its other viewers no tab at all. The same chart
    is what the page's Org Chart button draws (``views/community.py``).
    """

    section_icon = "fa-solid fa-sitemap"
    template_name = "socialhub/community_plugins/org_chart.html"
    tab = "chart"

    @staticmethod
    def _viewer(kwargs):
        return getattr(kwargs.get("request"), "user", None)

    def is_visible(self, **kwargs) -> bool:
        from toto.socialhub.models import CommunityPosition
        from toto.socialhub.permissions import may_moderate_community

        if not super().is_visible(**kwargs):
            return False
        viewer = self._viewer(kwargs)
        if not getattr(viewer, "is_authenticated", False):
            return False
        community = self.get_community_from_kwargs(**kwargs)
        return (CommunityPosition.objects.filter(community=community).exists()
                or may_moderate_community(viewer, community))

    def get_context(self, **kwargs) -> dict[str, Any]:
        from django.urls import reverse

        from toto.socialhub import org_chart
        from toto.socialhub.permissions import may_moderate_community

        context = super().get_context(**kwargs)
        community = kwargs["community"]
        may_manage = may_moderate_community(self._viewer(kwargs), community)
        context.update({
            "chart_rows": org_chart.chart_of(community),
            "chart_may_manage": may_manage,
            "chart_create_url": reverse("socialhub:position_create",
                                        kwargs={"slug": community.slug}),
            "chart_title_max": org_chart.TITLE_MAX,
            "chart_order_max": org_chart.ORDER_MAX,
        })
        if may_manage:
            from toto.people.models import Person

            # Every person, by name: who is assigned need not be a member.
            context["chart_people"] = list(Person.objects.order_by("display_name", "pk"))
        return context

