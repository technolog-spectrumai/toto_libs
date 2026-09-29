"""`ingress_locations`: the geospatial demo, full mode only, and idempotent.

A deployment in realistic mode gets no invented territories; full mode seeds
them once, and a second run finds its own rows rather than doubling them.
What it seeds is shared infrastructure, kept to no circle.
"""

from io import StringIO
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from toto.locations import access
from toto.locations.models import (
    HAS_GIS, Address, MapLayer, MapLayerPolygon, Route, RouteChain, Territory, Zone,
)

MODELS = (Address, Territory, Zone, Route, RouteChain, MapLayer, MapLayerPolygon)


@skipUnless(HAS_GIS, "the demo is geometry; the command refuses a GIS-off host")
class IngressLocationsTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("admin", "a@example.org", "pw")
        self.out = StringIO()

    def counts(self):
        return {model.__name__: model.objects.count() for model in MODELS}

    def test_realistic_mode_seeds_nothing(self):
        call_command("ingress_locations", mode="realistic", stdout=self.out)
        self.assertEqual(set(self.counts().values()), {0})

    def test_full_mode_seeds_once_and_a_rerun_doubles_nothing(self):
        call_command("ingress_locations", mode="full", stdout=self.out)
        first = self.counts()
        for name in ("Address", "Territory", "Zone", "Route", "RouteChain", "MapLayer"):
            with self.subTest(model=name):
                self.assertGreater(first[name], 0)
        call_command("ingress_locations", mode="full", stdout=self.out)
        self.assertEqual(self.counts(), first)

    def test_what_it_seeds_is_everyones(self):
        call_command("ingress_locations", mode="full", stdout=self.out)
        stranger = get_user_model().objects.create_user("stranger", password="x")
        self.assertEqual(access.readable_routes(stranger).count(), Route.objects.count())
        self.assertEqual(access.readable_layers(stranger).count(), MapLayer.objects.count())

    def test_every_seeded_polygon_has_a_centre_for_its_label(self):
        call_command("ingress_locations", mode="full", stdout=self.out)
        self.assertFalse(MapLayerPolygon.objects.filter(center__isnull=True).exists())
        self.assertFalse(Address.objects.filter(latitude__isnull=True).exists())
