"""What a page hands the map widget (``templates/geography/_map.html``).

One function builds the context for both pages that draw a map, the profile
and the community page: the saved points and the zone the viewer may see,
the doors the viewer may use, and the sentences the script shows. Nothing a
viewer may not see goes in: the caller passes only what ``access`` let
through.

THE PAGE'S DATA HOLDS WHAT THE PAGE DRAWS, and no more: a point is its pin
and the name on the pin; a zone is its outline. A point's note and a zone's
name and description are drawn for nobody on the map, so they are not in
``config``, which every viewer of the map can read in the page's source.
Whoever may set them reads them in the form, which the template fills from
``point`` and ``zone`` and only draws for that viewer.

ROUTE SEARCH IS ASKED FOR, NEVER GIVEN BY DEFAULT (the owner, 2026-10-06:
"Route search exists exclusively in the Locations app. Remove route-search
controls from SocialHub and other map views."). A page gets the route panel,
its price, its sentences and the address of the route door only when it
calls ``map_context(…, routes=True)`` and routing is on at the host. The
profile's map and the community's do not ask, so neither names the door.
"""

from __future__ import annotations

from django.urls import reverse
from django.utils.translation import gettext as _

from . import places, routing
from .saves import point_state, zone_state


def _texts(routes: bool) -> dict:
    texts = {
        "failed": _("That did not work. Try again."),
        "nothing_found": _("No place found. Nothing was charged."),
        "temporary": _("Temporary point"),
        "place_first": _("Click the map to place the point first."),
        "corners": _("%(n)s corners"),
    }
    if routes:
        texts.update({
            "no_route": _("No route was found between these two points. Nothing was charged."),
            "route_summary": _("%(km)s km, about %(min)s min"),
            "choose_end": _("Choose a point"),
            "end_gone": _("That point is no longer on the map"),
            "choose_again": _("Choose the points again"),
        })
    return texts


def point_of(address, kind, label="") -> dict | None:
    """A saved point as the widget draws it: where the pin is and the name
    on it. Not its note."""
    state = point_state(address)
    if state is None:
        return None
    return {"kind": kind, "lat": state["lat"], "lng": state["lng"],
            "label": state["name"] or label}


def map_context(key, *, points=(), zone=None, edit_kind="", point_urls=None,
                zone_urls=None, edited=None, routes: bool = False) -> dict:
    """The ``geo`` a template includes ``geography/_map.html`` with.

    ``points``: what ``point_of`` made, Nones dropped. ``zone``: a ``Zone``
    row or None. ``point_urls`` / ``zone_urls``: ``(save, clear)`` for a
    viewer who may set them, else None. ``edited``: the ``Address`` row the
    point form starts from. ``routes``: this page offers route search (where
    the host has routing on); without it the page holds no route panel, no
    route sentence and no address of the route door."""
    points = [point for point in points if point is not None]
    zone_data = zone_state(zone)
    outline = {"outline": zone_data["outline"]} if zone_data else None
    urls = {}
    search_on = places.enabled()
    route_modes = routing.modes() if routes and routing.enabled() else []
    if search_on:
        urls["search"] = reverse("geography:search")
    if route_modes:
        urls["route"] = reverse("geography:route")
    if point_urls:
        urls["savePoint"], urls["clearPoint"] = point_urls
    if zone_urls:
        urls["saveZone"], urls["clearZone"] = zone_urls
    center = [points[0]["lat"], points[0]["lng"]] if points else None
    return {
        "key": key,
        "config_id": f"{key}-config",
        "config": {
            "center": center, "zoom": 13, "points": points, "zone": outline,
            "urls": urls, "edit_kind": edit_kind,
            "can_edit_point": bool(point_urls), "can_edit_zone": bool(zone_urls),
            "texts": _texts(bool(route_modes)),
        },
        "search_enabled": search_on,
        "route_modes": route_modes,
        "can_edit_point": bool(point_urls),
        "can_edit_zone": bool(zone_urls),
        "point": point_state(edited),
        "zone": zone_data if zone_urls else None,
    }
