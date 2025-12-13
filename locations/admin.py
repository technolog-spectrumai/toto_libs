# locations/admin.py
from django.contrib import admin
from django.contrib.gis.admin import OSMGeoAdmin
from .models import PointFeature, ZoneFeature, PathFeature

@admin.register(PointFeature)
class PointFeatureAdmin(OSMGeoAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(ZoneFeature)
class ZoneFeatureAdmin(OSMGeoAdmin):
    list_display = ("id", "name")
    search_fields = ("name",)


@admin.register(PathFeature)
class PathFeatureAdmin(OSMGeoAdmin):
    list_display = ("id", "name")
    search_fields = ("name",) 