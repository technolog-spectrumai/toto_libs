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

THE ROUTER'S ANSWER IS CHECKED BEFORE IT IS CHARGED. It is read with a cap
and under a deadline (``provider``), and ``fetch`` takes it only as what the
page can draw: a list of routes whose first is an object, a line of at least
two pairs of finite numbers, a distance and a duration that are finite
numbers and not below zero. Anything else is a ValueError, which is a
failure of the router: 503, nothing charged, and never an answer the page
would be charged for and could not show.

Modes: car, bicycle, foot. No public transport.
"""

from __future__ import annotations

import http.client
import logging
import math
import time
from urllib.parse import urlencode
from urllib.request import Request

from django.conf import settings
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from . import audit, charging, metrics, provider, shapes
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

#: The most the router's answer may weigh. A drive across a continent is
#: about 240 bytes a kilometre with ``overview=full`` (measured against the
#: public router on 2026-10-06: Lisbon to Warsaw, 3,327 km, 0.8 MB), so the
#: longest road there is stays under this.
ANSWER_MAX = 8 * 1024 * 1024
#: The most that is read of the router's refusal, to tell "no route".
REFUSAL_MAX = 64 * 1024


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


def _measure(value, what) -> float:
    """A distance or a duration as the router gives it: a number, finite
    and not below zero. A missing one is no zero."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"the router's answer holds no {what}")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f"the router's {what} is no number") from None
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"the router's {what} is no number")
    return value


def _line(geometry) -> list[list[float]]:
    """The corners of the router's line, ``[[lng, lat], …]``: a GeoJSON
    LineString of at least two pairs of finite numbers."""
    if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
        raise ValueError("the router's answer holds no line")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("the router's line has no two points")
    line = []
    for pair in coordinates:
        if not isinstance(pair, list) or len(pair) < 2:
            raise ValueError("the router's line holds something that is no point")
        for number in pair[:2]:
            if isinstance(number, bool) or not isinstance(number, (int, float)) \
                    or not math.isfinite(number):
                raise ValueError("the router's line holds something that is no number")
        line.append([pair[0], pair[1]])
    return line


def fetch(start, end, mode, config) -> dict:
    """Ask the router. ``{"line", "distance_km", "duration_min"}``, where
    ``line`` is a GeoJSON LineString. Raises ``NoRoute`` or one of
    ``PROVIDER_ERRORS``; an answer that is not shaped as a route is a
    ValueError, so it is never charged."""
    endpoint = config["endpoints"][mode]
    coordinates = f"{start[1]},{start[0]};{end[1]},{end[0]}"
    query = urlencode({"overview": "full", "geometries": "geojson",
                       "steps": "false", "alternatives": "false"})
    # The router is an OpenStreetMap service too: its usage policy asks for
    # the identifying User-Agent the geocoder sends.
    request = Request(f"{endpoint}/{coordinates}?{query}",
                      headers=geocoding_headers(geocoding_settings()))
    timeout = config.get("timeout", 10)
    deadline = time.monotonic() + timeout
    try:
        payload = provider.fetch_json(request, timeout=timeout, limit=ANSWER_MAX)
    except http.client.HTTPException:
        raise
    except OSError as exc:
        # OSRM says "no route" with HTTP 400 and a JSON body.
        if getattr(exc, "code", None) == 400 and getattr(exc, "read", None) is not None:
            try:
                refusal = provider.parse(provider.read_capped(exc, REFUSAL_MAX, deadline))
            except (OSError, ValueError, AttributeError):
                refusal = None
            if isinstance(refusal, dict) and refusal.get("code") in ("NoRoute", "NoSegment"):
                raise NoRoute() from None
        raise
    if not isinstance(payload, dict):
        raise ValueError("the router's answer is not an object")
    if payload.get("code") in ("NoRoute", "NoSegment"):
        raise NoRoute()
    routes = payload.get("routes")
    if payload.get("code") != "Ok" or not isinstance(routes, list) or not routes \
            or not isinstance(routes[0], dict):
        raise ValueError("the router's answer holds no route")
    route = routes[0]
    return {
        "line": {"type": "LineString", "coordinates": _line(route.get("geometry"))},
        "distance_km": round(_measure(route.get("distance"), "distance") / 1000, 2),
        "duration_min": round(_measure(route.get("duration"), "duration") / 60, 1),
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
