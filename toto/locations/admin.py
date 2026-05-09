from django.contrib import admin
from toto.core.base_admin import TotoGeoAdmin, TotoModelAdmin

from .models import (
    Address,
    MapLayer,
    MapLayerPolygon,
    RouteChain,
    Territory,
    Zone,
    Route,
    Travel,
    Visit
)


class RouteInline(admin.TabularInline):
    model = Route
    extra = 0
    fields = ("sequence", "name", "start_address", "end_address", "geometry")
    autocomplete_fields = ("start_address", "end_address")
    ordering = ("sequence", "name")


class MapLayerPolygonInline(admin.TabularInline):
    model = MapLayerPolygon
    extra = 0
    fields = ("name", "value", "center", "geometry", "properties")
    ordering = ("name", "id")


@admin.register(Territory)
class TerritoryAdmin(TotoGeoAdmin):
    list_display = ("id", "name", "capital")
    list_display_links = ("id", "name")
    search_fields = ("name",)


@admin.register(Zone)
class ZoneAdmin(TotoGeoAdmin):
    list_display = ("id", "name", "territory")
    list_display_links = ("id", "name")
    search_fields = ("name", "territory__name")
    autocomplete_fields = ("territory",)


@admin.register(RouteChain)
class RouteChainAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "route_count")
    list_display_links = ("id", "name")
    search_fields = ("name", "description", "routes__name")
    inlines = (RouteInline,)

    def route_count(self, obj):
        return obj.routes.count()

    route_count.short_description = "Routes"


@admin.register(MapLayer)
class MapLayerAdmin(TotoModelAdmin):
    list_display = (
        "id",
        "name",
        "slug",
        "unit",
        "min_value",
        "max_value",
        "inverted_importance",
        "half_range",
        "is_active",
        "polygon_count",
    )
    list_display_links = ("id", "name")
    list_filter = ("is_active", "inverted_importance", "half_range")
    prepopulated_fields = {"slug": ("name",)}
    search_fields = ("name", "slug", "description")
    inlines = (MapLayerPolygonInline,)

    def polygon_count(self, obj):
        return obj.polygons.count()

    polygon_count.short_description = "Polygons"


@admin.register(MapLayerPolygon)
class MapLayerPolygonAdmin(TotoGeoAdmin):
    list_display = ("id", "name", "layer", "value", "center")
    list_display_links = ("id", "name")
    list_filter = ("layer",)
    search_fields = ("name", "layer__name", "layer__slug")
    autocomplete_fields = ("layer",)


@admin.register(Route)
class RouteAdmin(TotoGeoAdmin):
    list_display = ("id", "name", "route_chain", "sequence", "start_address", "end_address")
    list_display_links = ("id", "name")
    list_filter = ("route_chain",)
    search_fields = ("name", "route_chain__name", "start_address__street", "end_address__street")
    autocomplete_fields = ("route_chain", "start_address", "end_address")
    ordering = ("route_chain", "sequence", "name")


@admin.register(Address)
class AddressAdmin(TotoGeoAdmin):
    list_display = (
        "street",
        "building",
        "apartment",
        "locality_name",
        "state_or_province_name",
        "country_name",
    )
    list_display_links = ("street", "building")
    search_fields = (
        "street",
        "locality_name",
        "state_or_province_name",
        "country_name",
    )
    list_filter = ("country_name", "state_or_province_name")
    ordering = ("locality_name", "street")


@admin.register(Travel)
class TravelAdmin(TotoModelAdmin):
    list_display = (
        "id",
        "route",
        "starts_at",
        "ends_at",
        "participant_count",
    )
    list_display_links = ("id", "route")
    search_fields = (
        "info",
        "route__name",
        "participants__display_name",
        "participants__email",
    )
    list_filter = ("starts_at", "ends_at")
    autocomplete_fields = ("route",)
    filter_horizontal = ("participants",)
    ordering = ("-starts_at",)

    def participant_count(self, obj):
        return obj.participants.count()

    participant_count.short_description = "Participants"


@admin.register(Visit)
class VisitAdmin(TotoModelAdmin):
    list_display = (
        "id",
        "participant",
        "location",
        "score",
    )
    list_display_links = ("id", "participant")
    list_filter = ("score",)
    search_fields = (
        "participant__display_name",
        "participant__email",
        "location__street",
        "location__locality_name",
        "location__state_or_province_name",
        "location__country_name",
        "review",
    )
    autocomplete_fields = ("participant", "location")
    ordering = ("participant", "location")
