"""Who may change a location object — and, since 2026-09-30, who may read one.

**Reading goes by map domains** (`MapDomain`; the rule is
`toto.socialhub.clearance_access.group_gate`). Clearances go on a domain,
never on an item. Six kinds sit in domains: routes, map layers, addresses,
zones and territories here, and places in the host's `toto.places`:

* an item in **no kept domain** (no domain, or only domains without
  clearances) is every signed-in member's, as it always was;
* an item in **kept domains** is read by superusers and by whoever holds, for
  EVERY kept domain of the item, one of that domain's clearances — pessimistic.
  Not its creator, not a layer's owner, not staff.

On the map, in the JSON, in a list, a count, a picker or on a page, a hidden
item is a missing one (404). ``user`` None (a connector, a plugin with no
request) reads the open items only. Route chains are not in domains: a chain
is drawn and counted from the routes its reader may read.

Addresses are gated on the LOCATIONS doors only (the owner, 2026-09-30): a
person's profile, a community or an event shows its own address by its own
rule.

Writing is narrower (2026-09-25): an address or a route may have its metadata
and note changed by whoever created it, or by staff; a row from before
`created_by` existed has no creator and is staff's alone. Territories, zones
and route chains arrive from imported layers and the admin, so only staff
change them. Importing a map layer is a staff act. Map domains are made and
kept by superusers alone (the Domains tab).
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


def may_manage_domains(user) -> bool:
    """Map domains — their items and their clearances — are superusers' alone."""
    return bool(getattr(user, "is_authenticated", False) and user.is_superuser)


# ---------------------------------------------------------------------------
# Reading: map domains (2026-09-30)
# ---------------------------------------------------------------------------

#: The model name of each domain kind here -> (the domain's related name for
#: its through rows, the through row's FK to the item).
DOMAIN_ROWS = {
    "route": ("route_rows", "route"),
    "maplayer": ("map_layer_rows", "map_layer"),
    "address": ("address_rows", "address"),
    "zone": ("zone_rows", "zone"),
    "territory": ("territory_rows", "territory"),
}


def correlated_domains(rows: str, field: str):
    """The domains of the outer item — ``groups`` for ``group_gate``."""
    from django.db.models import OuterRef

    from .models import MapDomain

    return MapDomain.objects.filter(**{f"{rows}__{field}": OuterRef("pk")})


def domain_gate(user, queryset, *, rows: str, field: str, open=None):
    """The items of ``queryset`` that ``user`` may read, by their domains.
    ``open`` is the app's own rule for an item in no kept domain (None =
    everyone). The host's places use it with ``rows="place_rows"``."""
    from toto.socialhub.clearance_access import group_gate

    return group_gate(user, queryset, groups=correlated_domains(rows, field), open=open)


def domains_of(obj, *, rows: str | None = None, field: str | None = None):
    """The domains one item is in (a plain queryset)."""
    from .models import MapDomain

    if rows is None:
        rows, field = DOMAIN_ROWS[obj._meta.model_name]
    return MapDomain.objects.filter(**{f"{rows}__{field}": obj})


def domain_hidden(user, obj, *, rows: str | None = None, field: str | None = None) -> bool:
    """Whether its kept domains hide ``obj`` from ``user`` — the per-object
    twin of ``domain_gate``."""
    from toto.socialhub.clearance_access import group_hidden

    return group_hidden(user, domains_of(obj, rows=rows, field=field))


def _readable(model, user, queryset):
    qs = model.objects.all() if queryset is None else queryset
    rows, field = DOMAIN_ROWS[model._meta.model_name]
    return domain_gate(user, qs, rows=rows, field=field)


def readable_routes(user, queryset=None):
    from .models import Route

    return _readable(Route, user, queryset)


def readable_layers(user, queryset=None):
    from .models import MapLayer

    return _readable(MapLayer, user, queryset)


def readable_addresses(user, queryset=None):
    from .models import Address

    return _readable(Address, user, queryset)


def readable_zones(user, queryset=None):
    from .models import Zone

    return _readable(Zone, user, queryset)


def readable_territories(user, queryset=None):
    from .models import Territory

    return _readable(Territory, user, queryset)


def may_read(user, obj) -> bool:
    """The per-object twin, for any location object. A route chain is in no
    domain and is always readable (what it draws is its readable routes)."""
    if obj is None:
        return False
    if obj._meta.model_name not in DOMAIN_ROWS:
        return True
    return not domain_hidden(user, obj)


def readable_or_none(user, obj):
    """``obj`` when ``user`` may read it, else None — for an address or a
    territory one row points at (a route's ends, a territory's capital)."""
    return obj if obj is not None and may_read(user, obj) else None
