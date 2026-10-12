"""What connects the map to the pages of other apps (2026-10-12).

The three rows have been this app's since 2026-10-04 (``models.py``):
``Home`` (a person), ``EventPlace`` (an event) and ``CommunitySeat`` (a
community). Until that day they were keys on toto-base's own models and its
pages offered them: an event was scheduled at an address chosen from a list,
a community's page named its location. This module is the rules those pages
ask again, so the event form (``toto.events.forms``), a community's page
(``plugins/community_plugins.py``) and a profile
(``plugins/profile_plugins.py``) say the same thing:

* **who has the map**: ``map_open_to``. Somebody signed in whose plan holds
  the feature ``locations``, on a host that sells plans; everybody signed in
  on one that does not. The plan gate asks the same of every door under
  ``/locations/``, so a section is never drawn for somebody its own form
  would refuse;
* **what may be chosen**: ``registered_places``. The addresses the person
  may read on the map (``access.readable_addresses``: no home pin its
  resident shares with nobody, nothing of a map domain kept to clearances
  they do not hold). The rule of the Locations map and its pickers;
* **what is shown**: an address is drawn for a viewer who may read it, and
  for nobody else, whatever page it is linked from.
"""

from __future__ import annotations

from django.apps import apps

#: How many places a list offers. A page is not the map: past this, the
#: person picks on the Locations map.
PLACES_OFFERED = 500


def map_open_to(user) -> bool:
    """May ``user`` use the map and the sections it adds to other pages?"""
    if not getattr(user, "is_authenticated", False):
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    from django.conf import settings

    if not getattr(settings, "SUBSCRIPTION_ENFORCEMENT", True):
        return True
    from toto.subscriptions.gate import is_entitled

    try:
        return bool(is_entitled(user, "locations"))
    except Exception:  # noqa: BLE001 - a broken ladder closes the map, as the gate does a write
        return False


def registered_places(user):
    """The addresses ``user`` may choose from, in the order a list reads."""
    from .access import readable_addresses
    from .models import Address

    return readable_addresses(
        user, Address.objects.order_by("locality_name", "street", "building", "pk"))


def may_read_place(user, address) -> bool:
    from .access import readable_addresses
    from .models import Address

    if address is None:
        return False
    return readable_addresses(user, Address.objects.filter(pk=address.pk)).exists()


def point_of(address):
    """``(latitude, longitude)`` of an address, or None where it has none."""
    from .people_access import _coordinates

    if address is None:
        return None
    lat, lon = _coordinates(address)
    if lat is None or lon is None:
        return None
    return float(lat), float(lon)


# ---------------------------------------------------------------------------
# An event's place
# ---------------------------------------------------------------------------

def event_place(event):
    """The map address the event takes place at, or None."""
    from .models import EventPlace

    if event is None or event.pk is None:
        return None
    row = EventPlace.objects.filter(event=event).select_related("address").first()
    return row.address if row is not None else None


def set_event_place(event, address) -> None:
    """Tie the event to ``address``; None unties it."""
    from .models import EventPlace

    if address is None:
        EventPlace.objects.filter(event=event).delete()
    else:
        EventPlace.objects.update_or_create(event=event, defaults={"address": address})


def place_field(user, event=None):
    """The event form's list of registered places: a ``ModelChoiceField``
    over ``registered_places(user)``, not required."""
    from django import forms
    from django.utils.translation import gettext_lazy as _

    field = forms.ModelChoiceField(
        queryset=registered_places(user), required=False,
        label=_("Place on the map"), empty_label=_("No place on the map"),
        help_text=_("One of the places registered in Locations. Left empty, the event is "
                    "tied to no place on the map."))
    field.widget.attrs["data-testid"] = "event-place"
    current = event_place(event)
    if current is not None and may_read_place(user, current):
        field.initial = current.pk
    return field


def place_shown(user, event):
    """The event's place for this viewer: the address when they have the map
    and may read it, else None."""
    if not map_open_to(user):
        return None
    address = event_place(event)
    return address if may_read_place(user, address) else None


# ---------------------------------------------------------------------------
# A community's seat
# ---------------------------------------------------------------------------

def community_seat(community):
    """The community's ``CommunitySeat`` row, or None."""
    from .models import CommunitySeat

    if community is None or community.pk is None:
        return None
    return (CommunitySeat.objects.filter(community=community)
            .select_related("address", "territory").first())


def may_set_seat(user, community) -> bool:
    """Who decides for the community (its head, an administrator:
    ``socialhub.permissions.may_moderate_community``) and has the map."""
    from toto.socialhub.permissions import may_moderate_community

    return map_open_to(user) and may_moderate_community(user, community)


def set_community_seat(community, address, territory=None):
    """Put the community's seat at ``address`` within ``territory``; both
    None takes the row away."""
    from .models import CommunitySeat

    if address is None and territory is None:
        CommunitySeat.objects.filter(community=community).delete()
        return None
    row, _created = CommunitySeat.objects.update_or_create(
        community=community, defaults={"address": address, "territory": territory})
    return row
