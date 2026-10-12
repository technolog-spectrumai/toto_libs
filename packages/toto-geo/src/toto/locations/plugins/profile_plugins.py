"""Home on the map, on one's own profile (2026-10-04).

The map picker, the place search and the three-way sharing setting were the
"Where you live" section of the socialhub's Edit profile tab. toto-base
carries no geography now — a person's address there is text — so on a host
that installs this app they arrive as this plugin, on the owner's Overview,
posting to this app's own doors (``home_views``).
"""

from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.profile_plugins import ProfilePlugin


@ProfilePlugin.plugin(
    key="home_on_the_map",
    title=_("Where you live"),
    order=20,
)
class HomeOnTheMapProfilePlugin(ProfilePlugin):
    template_name = "locations/profile_plugins/home.html"
    section_icon = "fa-solid fa-map-pin"
    show_for_owner_only = True
    tab = "overview"

    def is_visible_for_profile(self, **kwargs) -> bool:
        # For somebody who has the map (2026-10-12): the section's forms post
        # to this app's doors, which the plan gate closes to anybody else.
        from toto.locations.links import map_open_to

        request = self.get_request_from_kwargs(**kwargs)
        return map_open_to(getattr(request, "user", None))

    def get_context(self, **kwargs):
        from toto.locations.geocode import geocoding_enabled, geocoding_settings
        from toto.locations.models import HomeSharing
        from toto.locations.people_access import home_address, sharing_of

        context = super().get_context(**kwargs)
        profile = kwargs["profile"]
        context["profile"] = profile
        context["home_address"] = home_address(profile)
        context["home_sharing"] = sharing_of(profile)
        context["sharing_choices"] = HomeSharing.choices
        context["geocoding_enabled"] = geocoding_enabled(geocoding_settings())
        return context


@ProfilePlugin.plugin(
    key="home_map",
    title=_("On the map"),
    order=21,
)
class HomeMapProfilePlugin(ProfilePlugin):
    """Where the person lives, drawn on a map on their profile (2026-10-12):
    for whoever has the map and may see it (``people_access``, the People
    map's own rule). The person themself sees their exact pin whatever they
    share; anybody else the pin of an exact sharer, the area of an
    approximate one, and nothing of somebody who shares nothing."""

    template_name = "locations/profile_plugins/map.html"
    section_icon = "fa-solid fa-map-location-dot"
    tab = "overview"

    def _shown(self, **kwargs):
        """``(point, approximate, label)`` for this viewer, or None."""
        from toto.locations import links
        from toto.locations.models import HomeSharing
        from toto.locations.people_access import (
            home_address, may_see_location, place_label, point_for, sharing_of,
        )

        request = self.get_request_from_kwargs(**kwargs)
        viewer = getattr(request, "user", None)
        profile = self.get_profile_from_kwargs(**kwargs)
        if not links.map_open_to(viewer) or not may_see_location(viewer, profile):
            return None
        if self.is_profile_owner(**kwargs):
            address = home_address(profile)
            point = links.point_of(address)
            return (point, False, str(address)) if point else None
        point = point_for(profile)
        if point is None:
            return None
        return point, sharing_of(profile) == HomeSharing.APPROXIMATE, place_label(profile)

    def is_visible_for_profile(self, **kwargs) -> bool:
        return self._shown(**kwargs) is not None

    def get_context(self, **kwargs):
        from django.urls import reverse

        context = super().get_context(**kwargs)
        point, approximate, label = self._shown(**kwargs)
        context.update({
            "home_point": point, "home_approximate": approximate, "home_label": label,
            "home_is_own": self.is_profile_owner(**kwargs),
            "home_map_id": "home-map",
            "people_map_url": reverse("locations:people"),
        })
        return context
