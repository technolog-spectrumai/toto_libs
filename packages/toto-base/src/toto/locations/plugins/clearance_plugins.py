"""Routes and map layers a new clearance may keep (2026-09-30; socialhub's
``ClearanceTargetPlugin``). Addresses stay shared infrastructure and are never
kept to a clearance."""

from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from toto.socialhub import clearance_access
from toto.socialhub.plugins.clearance_plugins import SEARCH_LIMIT, ClearanceTargetPlugin, add_to

ROWS = "clearance_rows"


class _LocationKind(ClearanceTargetPlugin):
    #: The kind name the locations views and audit use ("route", "maplayer").
    location_kind = ""

    def model(self):
        raise NotImplementedError

    def search(self, q, limit=SEARCH_LIMIT):
        return [self.row(obj) for obj in self.model().objects.filter(name__icontains=q)
                .order_by("name", "pk")[:limit]]

    def resolve(self, pks):
        return list(self.model().objects.filter(pk__in=pks).order_by("name", "pk"))

    def keep(self, objects, clearance, *, actor):
        for obj in objects:
            clearance_access.set_clearances(
                obj, add_to(clearance_access.clearances_of(obj, rows=ROWS), clearance),
                rows=ROWS, actor=actor, action=f"{self.location_kind}.clearances_changed",
                app_label="locations", kind=self.location_kind)
        return len(objects)


@ClearanceTargetPlugin.plugin(key="locations.route", title=_("Routes"), order=30)
class Routes(_LocationKind):
    icon = "route"
    location_kind = "route"

    def model(self):
        from toto.locations.models import Route

        return Route

    def label(self, obj):
        return obj.name or f"Route {obj.pk}"


@ClearanceTargetPlugin.plugin(key="locations.map_layer", title=_("Map layers"), order=31)
class MapLayers(_LocationKind):
    icon = "layer-group"
    location_kind = "maplayer"

    def model(self):
        from toto.locations.models import MapLayer

        return MapLayer

    def detail(self, obj):
        return obj.slug
