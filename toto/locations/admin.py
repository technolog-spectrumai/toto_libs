from django.contrib import admin
from django.contrib.gis.admin import OSMGeoAdmin

from .models import (
    Address,
    Territory,
    Route
)


@admin.register(Territory)
class TerritoryAdmin(OSMGeoAdmin):
    list_display = ("id", "name", "capital")
    list_display_links = ("id", "name")
    search_fields = ("name",)


@admin.register(Route)
class RouteAdmin(OSMGeoAdmin):
    list_display = ("id", "name", "start_address", "end_address")
    list_display_links = ("id", "name")
    search_fields = ("name", "start_address__street", "end_address__street")
    autocomplete_fields = ("start_address", "end_address")


@admin.register(Address)
class AddressAdmin(OSMGeoAdmin):
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
