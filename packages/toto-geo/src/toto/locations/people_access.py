"""Who may see where somebody lives, and how precisely.

ONE RULE, TWO SPELLINGS. `may_see_location` answers for one person and
`shared_people` answers for a queryset, and they must agree — the rule stated
at `vault/access.py:118-121`, for the same reason: a listing that is more
generous than the door publishes what the door refuses, and nobody notices,
because both pages render.

The shape is copied from `events/views.py:48-85`, which is where this exact bug
class was already found and fixed on this platform: a `public` flag that applied
to anonymous visitors only, while every signed-in user got `objects.all()`.

**The default is OFF.** A person appears here because they chose to, and for no
other reason. There is no "everyone in your community" fallback, no staff
override, and no inference from having filled in an address — an address is
filled in for delivery and administration, and treating it as consent to be
mapped is exactly the assumption this module exists to refuse.
"""

from __future__ import annotations

from django.db.models import Q

#: Decimal places kept for an APPROXIMATE sharer. Two places is ~1.1 km of
#: latitude — enough to say "the other side of town" and not enough to say
#: which building. Applied at READ time only; see `point_for`.
_COARSE_PLACES = 2


def _person_for(user):
    return getattr(user, "community_profile", None)


def home_of(person):
    """The person's ``Home`` row, or None. The address and the sharing switch
    were columns of Person until 2026-10-04; they are this app's own row now."""
    from django.core.exceptions import ObjectDoesNotExist

    if person is None or person.pk is None:
        return None
    try:
        return person.home
    except ObjectDoesNotExist:
        return None


def home_address(person):
    """The address the person lives at on the map, or None."""
    home = home_of(person)
    return home.address if home is not None and home.address_id else None


def sharing_of(person) -> str:
    """``HomeSharing``: what the person shares of their home — OFF with no
    Home row."""
    from .models import HomeSharing

    home = home_of(person)
    return home.sharing if home is not None else HomeSharing.OFF


def may_see_location(viewer, person) -> bool:
    """The door: may `viewer` see where `person` lives?"""
    if person is None or home_address(person) is None:
        return False
    if not getattr(viewer, "is_authenticated", False):
        return False

    from .models import HomeSharing

    # Your own row is always yours to see, whatever the setting — otherwise
    # switching sharing off would hide your own pin from you and read as a bug.
    own = _person_for(viewer)
    if own is not None and own.pk == person.pk:
        return True

    return sharing_of(person) != HomeSharing.OFF


def shared_people(viewer):
    """The listing: every person `viewer` may see, as a queryset.

    The same rule as `may_see_location`, spelled as a `Q`. `tests_people.py`
    asserts the two agree for every person, because this is precisely the pair
    that drifts.
    """
    from toto.people.models import Person

    from .models import HomeSharing

    if not getattr(viewer, "is_authenticated", False):
        return Person.objects.none()

    visible = Q(home__address__isnull=False) & ~Q(home__sharing=HomeSharing.OFF)

    own = _person_for(viewer)
    if own is not None:
        visible |= Q(pk=own.pk, home__address__isnull=False)

    return (Person.objects.filter(visible)
            .select_related("home__address")
            .distinct())


def _coordinates(address):
    """An address's point, whichever store actually holds it.

    `Address.save()` keeps the floats in step with `geometry`, so on a live GIS
    host they normally agree. They do NOT agree for a row geocoded before
    migration `0005_address_latlon` and never re-saved since: that row has a
    geometry and two NULL floats. Reading the floats alone would silently drop
    every address older than the migration.
    """
    lat, lon = address.latitude, address.longitude
    if lat is not None and lon is not None:
        return lat, lon
    geometry = getattr(address, "geometry", None)
    if geometry is not None:
        return geometry.y, geometry.x
    return None, None


def point_for(person):
    """The coordinates to publish for this person, or None.

    An APPROXIMATE sharer is coarsened HERE, on the way out, and the coarse
    point is what everything downstream uses — the marker AND the distance.
    Measuring from the true point while drawing a coarse one would hand the
    exact position back to anyone willing to ask twice from two places.

    Never written back to the row: `Address.save()` treats `geometry` as
    authoritative and would overwrite the floats from it anyway, but the real
    reason is that this is a view of the data, not a change to it.
    """
    from .models import HomeSharing

    address = home_address(person)
    if address is None:
        return None
    lat, lon = _coordinates(address)
    if lat is None or lon is None:
        return None
    sharing = sharing_of(person)
    if sharing == HomeSharing.APPROXIMATE:
        return round(lat, _COARSE_PLACES), round(lon, _COARSE_PLACES)
    if sharing == HomeSharing.EXACT:
        return lat, lon
    return None


def place_label(person) -> str:
    """What to call the place, at the precision the person allowed.

    An approximate sharer gets their locality, never their street: the label is
    as much of a disclosure as the pin, and a street name beside a coarse marker
    would undo the coarsening.
    """
    from .models import HomeSharing

    address = home_address(person)
    if address is None:
        return ""
    if sharing_of(person) == HomeSharing.EXACT:
        return str(address)
    return ", ".join(part for part in (address.locality_name,
                                       address.state_or_province_name) if part)
