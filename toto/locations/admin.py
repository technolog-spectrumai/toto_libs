from django.contrib import admin
from toto.core.base_admin import TotoGeoAdmin

from .models import (
    Address,
    Territory,
    Route
)


@admin.register(Territory)
class TerritoryAdmin(TotoGeoAdmin):
    list_display = ("id", "name", "capital")
    list_display_links = ("id", "name")
    search_fields = ("name",)


@admin.register(Route)
class RouteAdmin(TotoGeoAdmin):
    list_display = ("id", "name", "start_address", "end_address")
    list_display_links = ("id", "name")
    search_fields = ("name", "start_address__street", "end_address__street")
    autocomplete_fields = ("start_address", "end_address")


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
