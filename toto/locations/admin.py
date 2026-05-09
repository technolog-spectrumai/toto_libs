from django.contrib import admin
from toto.core.base_admin import TotoGeoAdmin

from .models import (
    Address,
    RouteChain,
    Territory,
    Zone,
    Route,
)


class RouteInline(admin.TabularInline):
    model = Route
    extra = 0
    fields = ("sequence", "name", "start_address", "end_address", "geometry")
    autocomplete_fields = ("start_address", "end_address")
    ordering = ("sequence", "name")


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
