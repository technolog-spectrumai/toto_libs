"""A community's headquarters and zone on its page (2026-10-06).

Key ``geography_headquarters``. The map with the headquarters pin and the
zone's outline and the search box, for every signed-in member, as the seat's
text is today. No route search: that is the Locations page's alone (the
owner, 2026-10-06), so this map does not ask ``map_context`` for it. The head and an administrator
(``socialhub.permissions.may_moderate_community``) also get the two forms.
Where neither is set and the viewer may set neither, nothing is drawn.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.community_plugins import CommunityPlugin


@CommunityPlugin.plugin(
    key="geography_headquarters",
    title=_("Headquarters and zone"),
    order=15,
)
class HeadquartersCommunityPlugin(CommunityPlugin):
    template_name = "geography/plugins/headquarters.html"
    section_icon = "fa-solid fa-map-location-dot"

    @staticmethod
    def _viewer(kwargs):
        request = kwargs.get("request")
        return getattr(request, "user", None)

    def is_visible(self, **kwargs) -> bool:
        from toto.geography.access import headquarters_of, may_set_headquarters

        if not super().is_visible(**kwargs):
            return False
        viewer = self._viewer(kwargs)
        if not getattr(viewer, "is_authenticated", False):
            return False
        community = self.get_community_from_kwargs(**kwargs)
        address, zone = headquarters_of(community)
        return address is not None or zone is not None \
            or may_set_headquarters(viewer, community)

    def get_context(self, **kwargs):
        from toto.geography.access import headquarters_of, may_set_headquarters
        from toto.geography.mapview import map_context, point_of

        context = super().get_context(**kwargs)
        community = self.get_community_from_kwargs(**kwargs)
        may_set = may_set_headquarters(self._viewer(kwargs), community)
        address, zone = headquarters_of(community)
        slug = {"slug": community.slug}
        context["geography_may_set"] = may_set
        context["geo"] = map_context(
            "geography-headquarters",
            points=[point_of(address, "headquarters", str(community.name))],
            zone=zone,
            edit_kind="headquarters",
            point_urls=((reverse("geography:headquarters", kwargs=slug),
                         reverse("geography:headquarters_clear", kwargs=slug))
                        if may_set else None),
            zone_urls=((reverse("geography:zone", kwargs=slug),
                        reverse("geography:zone_clear", kwargs=slug)) if may_set else None),
            edited=address if may_set else None,
        )
        return context
