"""Distance and radius over the shareholders, without GIS.

The suite's `toto.locations.nearby` owns the haversine (pure math, importable
on a GIS-off build); this module owns what The host asks of it: how far each
shareholder is from the company's headquarters, and who falls within a
radius. Kilometres in, kilometres out.

Missing data is a first-class answer, not an error: a company with no
headquarters yields no distances at all, and a party without coordinates is
returned in `unlocated` rather than silently dropped — the map's list
fallback shows both groups.
"""

from __future__ import annotations

from dataclasses import dataclass

from toto.locations.nearby import haversine_km

#: Same privacy reasoning as the suite's nearby module: an unbounded radius
#: turns "who is near the seat" into "everyone, ranked".
MAX_RADIUS_KM = 20000.0


def coordinates(address) -> tuple[float, float] | None:
    """A usable (lat, lon) pair, or None.

    Floats straight off the model can be None, NaN, or out of range —
    ingress data, hand-typed forms. Anything not plottable is None here,
    once, so no caller does its own half of the checking.
    """
    if address is None:
        return None
    lat, lon = address.latitude, address.longitude
    if lat is None or lon is None:
        return None
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return None
    if lat != lat or lon != lon:  # NaN
        return None
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return lat, lon


@dataclass(frozen=True)
class Placed:
    party: object
    latitude: float
    longitude: float
    distance_km: float | None  # None when the company has no headquarters


def survey(company, parties=None, *, radius_km=None):
    """Every shareholder, measured from the headquarters.

    Returns ``(placed, unlocated, origin)``:

    * ``placed`` — parties with usable coordinates, as `Placed` rows,
      nearest first (input order when there is no origin to measure from);
      when ``radius_km`` is given, rows beyond it are dropped.
    * ``unlocated`` — parties without usable coordinates, input order.
    * ``origin`` — the headquarters (lat, lon), or None.
    """
    origin = coordinates(getattr(company, "headquarters", None))
    if parties is None:
        parties = (company.parties.filter(active=True)
                   .select_related("location").order_by("name"))

    limit = None
    if radius_km is not None:
        try:
            limit = min(max(float(radius_km), 0.0), MAX_RADIUS_KM)
        except (TypeError, ValueError):
            limit = None

    placed, unlocated = [], []
    for party in parties:
        point = coordinates(party.location)
        if point is None:
            unlocated.append(party)
            continue
        distance = (haversine_km(origin[0], origin[1], point[0], point[1])
                    if origin else None)
        if limit is not None and (distance is None or distance > limit):
            continue
        placed.append(Placed(party, point[0], point[1], distance))

    if origin:
        placed.sort(key=lambda row: row.distance_km)
    return placed, unlocated, origin


def parse_radius(raw) -> float | None:
    """A radius from a query string: km > 0, or None for 'no filter'.

    Garbage is None, not an exception — ?radius=abc must never 500 a page.
    """
    if raw in (None, ""):
        return None
    try:
        value = float(str(raw).replace(",", "."))
    except (TypeError, ValueError):
        return None
    if value != value or value <= 0:
        return None
    return min(value, MAX_RADIUS_KM)
