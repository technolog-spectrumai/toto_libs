"""Who may change a location object — and, since 2026-09-29, who may read a
route or a map layer.

Everybody signed in reads the map — except the routes and map layers kept to
**circles** (`RouteCircle`, `MapLayerCircle`; the rule is
`toto.socialhub.circle_access`): those are seen by the circles' members, the
route's creator or the layer's owner, and superusers, and by nobody else. On
the map, in the JSON, in a list or on a page, a hidden one is a missing one.
Addresses, territories, zones and route chains are shared infrastructure
(people, events and communities point at addresses) and stay open.

Writing is narrower (2026-09-25): an
address or a route may have its metadata and note changed by whoever created
it, or by staff; a row from before `created_by` existed has no creator and is
staff's alone. Territories, zones and route chains arrive from imported
layers and the admin, so only staff change them. Importing a map layer is a
staff act — it creates shared rows everybody sees.
"""

from __future__ import annotations


def is_staff(user) -> bool:
    return bool(getattr(user, "is_authenticated", False)
                and (user.is_staff or user.is_superuser))


def may_write(user, obj) -> bool:
    if is_staff(user):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    creator = getattr(obj, "created_by_id", None)
    return creator is not None and creator == user.pk


def may_import_layer(user) -> bool:
    return is_staff(user)


# ---------------------------------------------------------------------------
# Reading: routes and map layers kept to circles (2026-09-29)
# ---------------------------------------------------------------------------

#: The kinds a circle may keep, by the detail page's `kind` name.
CIRCLED_KINDS = {"route": "route", "maplayer": "layer"}


def readable_routes(user, queryset=None):
    """Routes ``user`` may see: all but those kept to circles they are not
    in. ``user`` None (a connector, a plugin with no request): the open ones."""
    from django.db.models import Q

    from toto.socialhub.circle_access import gate

    from .models import Route

    qs = Route.objects.all() if queryset is None else queryset
    owner = Q(created_by=user) if getattr(user, "is_authenticated", False) else None
    return gate(user, qs, rows="circle_rows", owner=owner)


def readable_layers(user, queryset=None):
    """Map layers ``user`` may see, the same way; the owner is a Person."""
    from django.db.models import Q

    from toto.socialhub.circle_access import gate, person_of

    from .models import MapLayer

    qs = MapLayer.objects.all() if queryset is None else queryset
    person = person_of(user)
    owner = Q(owner=person) if person is not None else None
    return gate(user, qs, rows="circle_rows", owner=owner)


def may_read(user, obj) -> bool:
    """The per-object twin, for a route or a layer; anything else is open."""
    from toto.socialhub.circle_access import hidden, person_of

    from .models import MapLayer, Route

    if isinstance(obj, Route):
        return not hidden(user, obj, rows="circle_rows",
                          is_owner=bool(obj.created_by_id and obj.created_by_id == getattr(user, "pk", None)))
    if isinstance(obj, MapLayer):
        person = person_of(user)
        return not hidden(user, obj, rows="circle_rows",
                          is_owner=bool(person is not None and obj.owner_id == person.pk))
    return True


def may_manage_circles(user, obj) -> bool:
    """Who chooses a route's or a layer's circles: its creator or owner, and
    superusers (staff edit metadata and notes, not who reads)."""
    from toto.socialhub.circle_access import person_of

    from .models import MapLayer, Route

    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if isinstance(obj, Route):
        return bool(obj.created_by_id and obj.created_by_id == user.pk)
    if isinstance(obj, MapLayer):
        person = person_of(user)
        return bool(person is not None and obj.owner_id == person.pk)
    return False
