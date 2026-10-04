"""The kinds of item a map domain holds — a small plugin point (2026-09-30).

Locations registers its own five (route, map layer, address, zone,
territory); another app adds its kind from its own
``plugins/domain_plugins.py`` (the host's places), autodiscovered in
`LocationsConfig.ready`, so locations never imports it.

A kind is a `MapDomainKind` with an item model, the typed through table that
puts an item in a domain (never a generic key), and the names that join them.
The Domains tab talks to a kind only through:

* ``search(q, limit)`` -> ``[{pk, label, detail}]`` — superusers' picker;
* ``resolve(pks)`` -> the items with those pks;
* ``add(domain, objs)`` / ``remove(domain, objs)`` -> the items it changed;
* ``count(domain)`` and ``listed(domain, limit)`` — what the table shows.
"""

from __future__ import annotations

from typing import ClassVar

from django.apps import apps
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from toto.core.plugin import BasePlugin


class MapDomainKind(BasePlugin):
    registry: ClassVar[dict[str, "MapDomainKind"]] = {}

    #: "app_label.Model" of the item and of its through table to MapDomain.
    model_label: ClassVar[str] = ""
    through_label: ClassVar[str] = ""
    #: The through table's FK to the item (its FK to the domain is ``domain``).
    item_field: ClassVar[str] = ""
    #: What a search matches (``icontains``), and the order it answers in.
    search_fields: ClassVar[tuple] = ("name",)
    ordering: ClassVar[tuple] = ("name", "pk")
    icon: ClassVar[str] = "fa-solid fa-location-dot"

    @property
    def model(self):
        return apps.get_model(self.model_label)

    @property
    def through(self):
        return apps.get_model(self.through_label)

    def queryset(self):
        """The items, with what ``label``/``detail`` read joined in."""
        return self.model.objects.all()

    def label(self, obj) -> str:
        return str(obj)

    def detail(self, obj) -> str:
        return ""

    def row(self, obj) -> dict:
        return {"pk": obj.pk, "label": self.label(obj), "detail": self.detail(obj)}

    def search(self, q: str, limit: int = 20) -> list[dict]:
        q = " ".join((q or "").split())
        if not q:
            return []
        match = Q()
        for name in self.search_fields:
            match |= Q(**{f"{name}__icontains": q})
        if q.isdigit():
            match |= Q(pk=int(q))
        return [self.row(obj) for obj in self.queryset().filter(match).order_by(*self.ordering)[:limit]]

    def resolve(self, pks) -> list:
        return list(self.queryset().filter(pk__in=list(pks)).order_by(*self.ordering))

    def _members(self, domain):
        return self.through.objects.filter(domain=domain)

    def add(self, domain, objs) -> list:
        have = set(self._members(domain).filter(**{f"{self.item_field}__in": objs})
                   .values_list(f"{self.item_field}_id", flat=True))
        added = [obj for obj in objs if obj.pk not in have]
        self.through.objects.bulk_create([self.through(domain=domain, **{self.item_field: obj})
                                          for obj in added])
        return added

    def remove(self, domain, objs) -> list:
        rows = self._members(domain).filter(**{f"{self.item_field}__in": objs})
        gone = set(rows.values_list(f"{self.item_field}_id", flat=True))
        rows.delete()
        return [obj for obj in objs if obj.pk in gone]

    def count(self, domain) -> int:
        return self._members(domain).count()

    def listed(self, domain, limit: int = 100) -> list[dict]:
        items = (self.queryset().filter(domain_rows__domain=domain)
                 .order_by(*self.ordering)[:limit])
        return [self.row(obj) for obj in items]


@MapDomainKind.plugin(key="route", title=_("Route"), order=10)
class RouteKind(MapDomainKind):
    model_label = "locations.Route"
    through_label = "locations.RouteInDomain"
    item_field = "route"
    search_fields = ("name", "route_chain__name")
    icon = "fa-solid fa-route"

    def queryset(self):
        return self.model.objects.select_related("route_chain")

    def detail(self, obj) -> str:
        return obj.route_chain.name if obj.route_chain_id else ""


@MapDomainKind.plugin(key="map_layer", title=_("Map layer"), order=20)
class MapLayerKind(MapDomainKind):
    model_label = "locations.MapLayer"
    through_label = "locations.MapLayerInDomain"
    item_field = "map_layer"
    search_fields = ("name", "slug")
    icon = "fa-solid fa-layer-group"

    def detail(self, obj) -> str:
        return obj.slug


@MapDomainKind.plugin(key="address", title=_("Address"), order=40)
class AddressKind(MapDomainKind):
    model_label = "locations.Address"
    through_label = "locations.AddressInDomain"
    item_field = "address"
    search_fields = ("street", "building", "locality_name", "state_or_province_name",
                     "country_name")
    ordering = ("locality_name", "street", "building", "pk")
    icon = "fa-solid fa-location-dot"


@MapDomainKind.plugin(key="zone", title=_("Zone"), order=50)
class ZoneKind(MapDomainKind):
    model_label = "locations.Zone"
    through_label = "locations.ZoneInDomain"
    item_field = "zone"
    search_fields = ("name", "territory__name")
    icon = "fa-solid fa-draw-polygon"

    def queryset(self):
        return self.model.objects.select_related("territory")

    def detail(self, obj) -> str:
        return obj.territory.name if obj.territory_id else ""


@MapDomainKind.plugin(key="territory", title=_("Territory"), order=60)
class TerritoryKind(MapDomainKind):
    model_label = "locations.Territory"
    through_label = "locations.TerritoryInDomain"
    item_field = "territory"
    icon = "fa-solid fa-flag"
