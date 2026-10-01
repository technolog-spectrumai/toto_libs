"""The admin's maps come from the platform (2026-10-01, 37c.20).

TotoGeoAdmin was OSMGeoAdmin, which fetched OpenLayers 2 from
cdnjs.cloudflare.com, and the map layer's and route chain's inline rows used
Django's default geometry widget, OpenLayers 7 from cdn.jsdelivr.net over
NASA's tiles. Every geometry field in the admin is now drawn by
`toto.core.base_admin.LocalOSMWidget` — Django's OSMWidget with the image's
OpenLayers (`vendor/openlayers/`) — through `MapWidgetMixin`, on a ModelAdmin
and on an inline alike.
"""

from __future__ import annotations

from unittest import skipIf

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase

from toto.core.base_admin import LocalOSMWidget


@skipIf(LocalOSMWidget is None, "a GIS-off build draws no maps")
class LocalMapWidgetTests(SimpleTestCase):
    def test_openlayers_comes_from_the_platforms_static_files(self):
        media = str(LocalOSMWidget().media)
        self.assertNotIn("http", media)
        for path in ("vendor/openlayers/ol.js", "vendor/openlayers/ol.css",
                     "gis/js/OLMapWidget.js", "gis/css/ol3.css"):
            self.assertIn(f"/static/{path}", media)

    def test_its_tiles_are_openstreetmaps(self):
        html = LocalOSMWidget().render("geometry", None, attrs={"id": "id_geometry"})
        self.assertIn("new ol.source.OSM()", html)
        self.assertNotIn("earthdata.nasa.gov", html)

    def test_every_geometry_field_of_the_locations_admin_gets_it(self):
        from django.contrib.gis.db.models import GeometryField

        request = RequestFactory().get("/admin/")
        request.user = get_user_model()(is_superuser=True, is_staff=True, is_active=True)
        seen = []
        for model, model_admin in admin.site._registry.items():
            if model._meta.app_label != "locations":
                continue
            for owner in (model_admin, *model_admin.get_inline_instances(request)):
                for field in owner.model._meta.get_fields():
                    if not isinstance(field, GeometryField):
                        continue
                    with self.subTest(admin=type(owner).__name__, field=field.name):
                        formfield = owner.formfield_for_dbfield(field, request)
                        self.assertIsInstance(formfield.widget, LocalOSMWidget)
                        seen.append(type(owner).__name__)
        for name in ("ZoneAdmin", "AddressAdmin", "RouteInline", "MapLayerPolygonInline"):
            self.assertIn(name, seen)
