"""A person's point on their profile (2026-10-06).

On the Overview, key ``geography_address``. The owner sees the map with a
pin they can move, the search box, the route panel, a name and a note, Save
and Remove. Another member sees the map only when ``access.visible_point``
says they may see the point (``Person.show_address``); otherwise the section
is not drawn at all, so the page holds no map, no Leaflet tag and no map
data.
"""

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="geography_address",
    title=_("On the map"),
    order=20,
)
class AddressProfilePlugin(ProfilePlugin):
    template_name = "geography/plugins/address.html"
    section_icon = "fa-solid fa-map-pin"
    tab = "overview"

    def is_visible_for_profile(self, **kwargs) -> bool:
        from toto.geography.access import visible_point

        if self.is_profile_owner(**kwargs):
            return True
        request = self.get_request_from_kwargs(**kwargs)
        profile = self.get_profile_from_kwargs(**kwargs)
        if request is None or not getattr(profile, "pk", None):
            return False
        return visible_point(request.user, profile) is not None

    def get_context(self, **kwargs):
        from toto.geography.access import visible_point
        from toto.geography.mapview import map_context, point_of

        context = super().get_context(**kwargs)
        request = self.get_request_from_kwargs(**kwargs)
        profile = self.get_profile_from_kwargs(**kwargs)
        own = self.is_profile_owner(**kwargs)
        address = visible_point(request.user, profile)
        context["geography_own"] = own
        context["geography_has_point"] = address is not None
        context["geography_shown"] = bool(profile.show_address)
        context["geo"] = map_context(
            "geography-address",
            points=[point_of(address, "address", str(profile.display_name or ""))],
            edit_kind="address",
            point_urls=((reverse("geography:my_address"),
                         reverse("geography:my_address_clear")) if own else None),
            edited=address if own else None,
        )
        return context
