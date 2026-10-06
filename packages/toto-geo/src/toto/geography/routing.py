"""Route search: calculated, answered and forgotten.

A route between two points is asked of the host's router
(``GEOGRAPHY_ROUTING``: an OSRM endpoint per mode, FOSSGIS's public one by
default) and answered as a line with its distance and duration. The line is
written to no model, no cache, no file, no log and no audit record.

BOTH ENDS ARE COORDINATES, ``{"lat", "lng"}``, checked before anything else.
A row id or a name in an end is a 400, so this door cannot be asked where a
hidden home or another community's pin is: the browser sends the two points
it already holds.

The doors, cheapest refusal first::

    signed in -> routing on here -> ends, mode and op valid
    -> per-member limit (10 a minute)
    -> known op: a replay (calculated again, charged nothing more), or 409
    -> fresh op: quota and funds (402 before the router is asked)
    -> the router, behind one throttle for everybody (one call a second)
    -> "no route" is answered, uncharged; a failure is 503, uncharged
    -> one usage event and one charge; a ledger refusal withholds the line

Modes: car, bicycle, foot. No public transport.
"""

from __future__ import annotations

import http.client
import json
import logging
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from . import audit, charging, metrics, shapes
from .charging import Refusal
from .geocode import geocoding_headers, geocoding_settings
from .places import require_member, throttle_member, wait_for_provider

logger = logging.getLogger(__name__)

DEFAULT_ROUTING_SETTINGS = {
    "enabled": True,
    "endpoints": {
        "car": "https://routing.openstreetmap.de/routed-car/route/v1/driving",
        "bicycle": "https://routing.openstreetmap.de/routed-bike/route/v1/driving",
        "foot": "https://routing.openstreetmap.de/routed-foot/route/v1/driving",
    },
    "timeout": 10,
}

MODES = (
    ("car", gettext_lazy("Car")),
    ("bicycle", gettext_lazy("Bicycle")),
    ("foot", gettext_lazy("On foot")),
)

USER_LIMIT = 10
USER_WINDOW = 60
PROVIDER_KEY = "geography:route:provider"

ROUTE_LABEL = "Route search"

#: The same set the geocoder fails with (``geocode.PROVIDER_ERRORS``).
PROVIDER_ERRORS = (OSError, http.client.HTTPException, ValueError)


class NoRoute(Exception):
    """The router answered and found no way between the two points."""


def routing_settings() -> dict:
    config = dict(DEFAULT_ROUTING_SETTINGS)
    config.update(getattr(settings, "GEOGRAPHY_ROUTING", {}) or {})
    return config


def enabled() -> bool:
    config = routing_settings()
    return bool(config.get("enabled")) and bool(config.get("endpoints"))


def modes() -> list[tuple[str, str]]:
    """The modes this host has an endpoint for, in their order."""
    endpoints = routing_settings().get("endpoints") or {}
    return [(key, label) for key, label in MODES if endpoints.get(key)]


def fetch(start, end, mode, config) -> dict:
    """Ask the router. ``{"line", "distance_km", "duration_min"}``, where
    ``line`` is a GeoJSON LineString. Raises ``NoRoute`` or one of
    ``PROVIDER_ERRORS``."""
    endpoint = config["endpoints"][mode]
    coordinates = f"{start[1]},{start[0]};{end[1]},{end[0]}"
    query = urlencode({"overview": "full", "geometries": "geojson",
                       "steps": "false", "alternatives": "false"})
    # The router is an OpenStreetMap service too: its usage policy asks for
    # the identifying User-Agent the geocoder sends.
    request = Request(f"{endpoint}/{coordinates}?{query}",
                      headers=geocoding_headers(geocoding_settings()))
    try:
        with urlopen(request, timeout=config.get("timeout", 10)) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except http.client.HTTPException:
        raise
    except OSError as exc:
        # OSRM says "no route" with HTTP 400 and a JSON body.
        body = getattr(exc, "read", None)
        if getattr(exc, "code", None) == 400 and body is not None:
            try:
                if json.loads(exc.read().decode("utf-8")).get("code") in ("NoRoute", "NoSegment"):
                    raise NoRoute() from None
            except (ValueError, AttributeError):
                pass
        raise
    if not isinstance(payload, dict):
        raise ValueError("the router's answer is not an object")
    if payload.get("code") in ("NoRoute", "NoSegment"):
        raise NoRoute()
    routes = payload.get("routes")
    if payload.get("code") != "Ok" or not routes:
        raise ValueError("the router's answer holds no route")
    route = routes[0]
    line = route.get("geometry")
    if not isinstance(line, dict) or line.get("type") != "LineString":
        raise ValueError("the router's answer holds no line")
    return {
        "line": {"type": "LineString", "coordinates": line.get("coordinates") or []},
        "distance_km": round(float(route.get("distance", 0)) / 1000, 2),
        "duration_min": round(float(route.get("duration", 0)) / 60, 1),
    }


def route(user, start, end, mode, op) -> dict:
    """``{"line", "distance_km", "duration_min", "charged"}``; ``line`` is
    None when the router found no way. Nothing of it is kept."""
    require_member(user)
    config = routing_settings()
    if not config.get("enabled") or not config.get("endpoints"):
        raise Refusal(_("Route search is not available on this server."), 404)
    try:
        start, end = shapes.clean_end(start), shapes.clean_end(end)
    except shapes.BadShape as exc:
        raise Refusal(str(exc), 400) from None
    if not isinstance(mode, str) or not config["endpoints"].get(mode) \
            or mode not in dict(MODES):
        raise Refusal(_("Choose car, bicycle or on foot."), 400)
    if start == end:
        raise Refusal(_("Choose two different points."), 400)
    op = charging.clean_op(op)
    throttle_member(user, "route", USER_LIMIT, USER_WINDOW)

    body = {"from": list(start), "to": list(end), "mode": mode}
    replay = charging.known(user, op, body)
    if not replay:
        charging.afford(user, metrics.ROUTE)

    wait_for_provider(PROVIDER_KEY)
    try:
        answer = fetch(start, end, mode, config)
    except NoRoute:
        return {"line": None, "distance_km": None, "duration_min": None, "charged": False}
    except PROVIDER_ERRORS as exc:
        # Neither end is logged: where a member is going is theirs.
        logger.warning("routing: the router failed: %s", type(exc).__name__)
        raise Refusal(_("Route search is not answering. Try again later; "
                        "this search was not charged."), 503) from None

    if replay:
        return {**answer, "charged": False}
    try:
        _nothing, charged = charging.settle(user, metrics.ROUTE, op, body, ROUTE_LABEL)
    except charging.Duplicate:
        return {**answer, "charged": False}
    audit.record(audit.ROUTE, user, metric=metrics.ROUTE,
                 amount=charging.amount_of(charged), outcome="charged")
    return {**answer, "charged": True}
