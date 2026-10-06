"""The two shapes this app stores, checked: a point and one closed ring.

No GEOS at module level: the doors import this to refuse bad input before
anything else is touched.
"""

from __future__ import annotations

import math

from django.utils.translation import gettext as _

SRID = 4326  # WGS84, what the map and both outside services speak

#: A coordinate is kept to six decimals, about ten centimetres: the same
#: number comes back from the database as went in, so "nothing changed" can
#: be told.
DECIMALS = 6

#: The most corners a zone's outline may have.
MAX_CORNERS = 500


class BadShape(ValueError):
    """Input that is no point or no outline. ``str(exc)`` is a sentence."""

    status_code = 400


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BadShape(_("Latitude and longitude must be numbers."))
    value = float(value)
    if not math.isfinite(value):
        raise BadShape(_("Latitude and longitude must be numbers."))
    return value


def clean_pair(lat, lng) -> tuple[float, float]:
    """``(lat, lng)`` as two floats on the globe, rounded to ``DECIMALS``."""
    lat, lng = _number(lat), _number(lng)
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise BadShape(_("That point is not on the globe."))
    return round(lat, DECIMALS), round(lng, DECIMALS)


def clean_end(value) -> tuple[float, float]:
    """A route's end: an object with ``lat`` and ``lng`` and nothing else.

    A row id, a name or an address in it is refused, so the route door can
    never be asked where a stored point is."""
    if not isinstance(value, dict) or set(value) != {"lat", "lng"}:
        raise BadShape(_("A route runs between two points given as latitude and longitude."))
    return clean_pair(value["lat"], value["lng"])


def clean_outline(value) -> list[tuple[float, float]]:
    """A zone's outline as its corners, ``[(lat, lng), …]``, the ring open.

    ``value`` is a list of ``[lat, lng]`` pairs in the order they were
    drawn; a last corner equal to the first is dropped. At least three
    corners, at most ``MAX_CORNERS``, no corner twice."""
    if not isinstance(value, list):
        raise BadShape(_("A zone's outline is a list of corners."))
    if len(value) > MAX_CORNERS + 1:
        raise BadShape(_("A zone's outline has at most %(n)d corners.") % {"n": MAX_CORNERS})
    corners = []
    for corner in value:
        if not isinstance(corner, (list, tuple)) or len(corner) != 2:
            raise BadShape(_("Each corner of an outline is a latitude and a longitude."))
        corners.append(clean_pair(corner[0], corner[1]))
    if len(corners) > 1 and corners[0] == corners[-1]:
        corners.pop()
    if len(corners) > MAX_CORNERS:
        raise BadShape(_("A zone's outline has at most %(n)d corners.") % {"n": MAX_CORNERS})
    if len(corners) < 3 or len(set(corners)) != len(corners):
        raise BadShape(_("A zone's outline needs at least three different corners."))
    return corners


def point_of(lat, lng):
    """The GEOS point of a cleaned pair."""
    from django.contrib.gis.geos import Point

    return Point(lng, lat, srid=SRID)


def polygon_of(corners):
    """The GEOS polygon of cleaned corners: one ring, closed here, and
    refused unless it is valid and simple (no edge crosses another)."""
    from django.contrib.gis.geos import LinearRing, Polygon

    ring = [(lng, lat) for lat, lng in corners]
    ring.append(ring[0])
    try:
        polygon = Polygon(LinearRing(ring), srid=SRID)
    except Exception:  # noqa: BLE001 - GEOS refuses a degenerate ring
        raise BadShape(_("That outline is not a closed shape.")) from None
    if not polygon.valid or not polygon.simple or polygon.area == 0:
        raise BadShape(_("A zone's outline may not cross itself."))
    return polygon


def pair_of(point) -> tuple[float, float]:
    """``(lat, lng)`` of a stored point, rounded as it was saved."""
    return round(point.y, DECIMALS), round(point.x, DECIMALS)


def corners_of(polygon) -> list[list[float]]:
    """``[[lat, lng], …]`` of a stored outline, the ring open."""
    ring = list(polygon[0].coords)[:-1]
    return [[round(lat, DECIMALS), round(lng, DECIMALS)] for lng, lat in ring]
