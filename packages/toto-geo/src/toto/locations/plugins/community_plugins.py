"""A community's seat on the map, on the community's page (2026-10-12).

Key ``seat_on_the_map``. The seat was ``Community.location`` until
2026-10-04, a line of text on this page set in the admin panel; it is this
app's ``CommunitySeat`` row, and this section is where it is seen and set:
the place on a map, its name and the territory, for whoever has the map and
may read the place (``links``); and for the community's head and an
administrator a list of the registered places to choose the seat from.
Nothing is drawn where there is no seat the viewer may read and they may not
set one, and nothing for somebody without the map.
"""

from django.utils.translation import gettext_lazy as _

from toto.socialhub.plugins.community_plugins import CommunityPlugin


@CommunityPlugin.plugin(
    key="seat_on_the_map",
    title=_("On the map"),
    order=16,
)
class SeatOnTheMapCommunityPlugin(CommunityPlugin):
    template_name = "locations/plugins/seat.html"
    section_icon = "fa-solid fa-map-location-dot"

    @staticmethod
    def _viewer(kwargs):
        return getattr(kwargs.get("request"), "user", None)

    @staticmethod
    def _seat_for(viewer, community):
        """``(address, territory)`` of the seat as this viewer may read it."""
        from toto.locations import links
        from toto.locations.access import readable_territories

        row = links.community_seat(community)
        if row is None:
            return None, None
        address = row.address if links.may_read_place(viewer, row.address) else None
        territory = row.territory
        if territory is not None and not readable_territories(viewer).filter(pk=territory.pk).exists():
            territory = None
        return address, territory

    def is_visible(self, **kwargs) -> bool:
        from toto.locations import links

        if not super().is_visible(**kwargs):
            return False
        viewer = self._viewer(kwargs)
        if not links.map_open_to(viewer):
            return False
        community = self.get_community_from_kwargs(**kwargs)
        if links.may_set_seat(viewer, community):
            return True
        address, territory = self._seat_for(viewer, community)
        return address is not None or territory is not None

    def get_context(self, **kwargs):
        from django.urls import reverse

        from toto.locations import links
        from toto.locations.access import readable_territories

        context = super().get_context(**kwargs)
        viewer = self._viewer(kwargs)
        community = self.get_community_from_kwargs(**kwargs)
        address, territory = self._seat_for(viewer, community)
        may_set = links.may_set_seat(viewer, community)
        context.update({
            "seat_community": community,
            "seat_address": address,
            "seat_point": links.point_of(address),
            "seat_territory": territory,
            "seat_may_set": may_set,
            "seat_map_id": "seat-map",
            "seat_locations_url": reverse("locations:locations_all"),
        })
        if address is not None:
            context["seat_address_url"] = reverse("locations:address_detail", args=[address.pk])
        if may_set:
            context["seat_places"] = list(links.registered_places(viewer)[:links.PLACES_OFFERED])
            context["seat_territories"] = list(readable_territories(viewer).order_by("name")[:links.PLACES_OFFERED])
            context["seat_new_place_url"] = reverse("locations:address_create")
        return context
