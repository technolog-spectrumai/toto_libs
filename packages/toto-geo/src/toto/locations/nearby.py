"""Finding people within a distance of a point.

The first proximity query in this repo — there was no distance, radius or
`dwithin` code anywhere before this, only OSRM's route length coming back over
HTTP.

**GIS only, deliberately.** `locations/urls.py` already replaces every route's
callback with a 404 when `HAS_GIS` is off, so nothing here is reachable on a
GIS-off build. A haversine fallback would be dead code pretending to be a
feature; if the app ever degrades per-view instead of blanket-404ing, this is
the module that grows one, and `_coordinates` in `people_access` is what it
would read.

**Distance is measured from the point we PUBLISH, not the one we store.** For an
approximate sharer those differ by up to ~1 km, and using the true point would
leak the precision that coarsening exists to remove: two searches from two
places would triangulate it. So this filters on a generous bounding query and
then measures in Python from `point_for`, which is the only function that knows
what a given viewer is allowed to see.
"""

from __future__ import annotations

import math

#: Mean Earth radius, kilometres.
_EARTH_KM = 6371.0088

#: Refuse a radius beyond this. Not a performance guard — a privacy one: an
#: unbounded radius turns "who is near me" into "everybody who shares, ranked",
#: which is a different feature nobody consented to.
MAX_RADIUS_KM = 100


def haversine_km(lat1, lon1, lat2, lon2) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def people_within(viewer, *, latitude, longitude, radius_km, community=None):
    """Every person `viewer` may see within `radius_km` of a point.

    Returns `[(person, distance_km), ...]`, nearest first.

    The queryset half comes from `people_access.shared_people`, so this cannot
    be more generous than the door — it only narrows. A caller that built its
    own queryset here is how the two would drift.
    """
    from .people_access import _person_for, point_for, shared_people

    radius_km = max(0.0, min(float(radius_km or 0), MAX_RADIUS_KM))

    people = shared_people(viewer)

    # You are not a result of "who is near me". `shared_people` includes you on
    # purpose — it is the twin of a door that must let you see your own row, and
    # the profile page needs that — but a search for neighbours that returns the
    # searcher is noise, and it would count you in "N people within 5 km".
    # Excluded HERE rather than there, so the door and its queryset stay
    # identical and only this caller narrows.
    own = _person_for(viewer)
    if own is not None:
        people = people.exclude(pk=own.pk)

    if community is not None:
        people = people.filter(communities=community)

    # The database narrows by a bounding box on the STORED point, which is
    # cheap and indexed; the exact test happens below on the PUBLISHED point.
    # The box is widened by the coarsening grid so an approximate sharer whose
    # published point falls inside the circle is never dropped because their
    # stored point sat just outside the box.
    people = people.filter(_box(latitude, longitude, radius_km + 2))

    found = []
    for person in people:
        point = point_for(person)
        if point is None:
            continue
        distance = haversine_km(latitude, longitude, point[0], point[1])
        if distance <= radius_km:
            found.append((person, distance))
    found.sort(key=lambda pair: pair[1])
    return found


def _box(latitude, longitude, radius_km):
    """A latitude/longitude window that certainly contains the circle.

    Longitude degrees shrink towards the poles, so the window is widened by
    1/cos(latitude); clamped because that term runs away at the pole and a
    degenerate window would exclude everything rather than everything-but.

    Rows with NULL floats are let THROUGH rather than filtered out. An address
    geocoded before migration `0005_address_latlon` and never re-saved has a
    geometry and no floats, and `point_for` resolves it from the geometry — so
    excluding it here would silently drop every address older than that
    migration, which is the same bug `_coordinates` exists to prevent. The
    exact test below discards whatever the box let through wrongly, and the set
    is small because only opt-in sharers reach this at all.
    """
    from django.db.models import Q

    lat_span = radius_km / 111.32
    shrink = max(math.cos(math.radians(latitude)), 0.01)
    lon_span = radius_km / (111.32 * shrink)
    inside = Q(
        home__address__latitude__gte=latitude - lat_span,
        home__address__latitude__lte=latitude + lat_span,
        home__address__longitude__gte=longitude - lon_span,
        home__address__longitude__lte=longitude + lon_span,
    )
    return inside | Q(home__address__latitude__isnull=True)
