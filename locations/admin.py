# locations/admin.py
from django.contrib import admin
from django.contrib.gis.admin import OSMGeoAdmin
from .models import PointFeature, ZoneFeature, PathFeature, Address
from toto.admin import BaseSerializableAdmin


@admin.register(PointFeature)
class PointFeatureAdmin(OSMGeoAdmin, BaseSerializableAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(ZoneFeature)
class ZoneFeatureAdmin(OSMGeoAdmin, BaseSerializableAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(PathFeature)
class PathFeatureAdmin(OSMGeoAdmin, BaseSerializableAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(Address)
class AddressAdmin(BaseSerializableAdmin):
    list_display = ('street', 'building', 'apartment', 'locality_name', 'state_or_province_name', 'country_name')
    search_fields = ('street', 'locality_name', 'state_or_province_name', 'country_name')
    list_filter = ('country_name', 'state_or_province_name')
    ordering = ('locality_name', 'street')