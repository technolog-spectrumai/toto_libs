"""`ingress_locations`: the geospatial demo, full mode only, and idempotent.

A deployment in realistic mode gets no invented territories; full mode seeds
them once, and a second run finds its own rows rather than doubling them.
What it seeds is shared infrastructure, in no kept map domain. Every seeded
platform (realistic or full, never mode none) gets one map domain,
``regulated_domain``, keeping nothing (2026-09-30).
"""

from io import StringIO
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase

from toto.locations import access
from toto.locations.models import (
    HAS_GIS, Address, MapDomain, MapLayer, MapLayerPolygon, Route, RouteChain, Territory, Zone,
)

from toto.locations.plugins.domain_plugins import MapDomainKind

MODELS = (Address, Territory, Zone, Route, RouteChain, MapLayer, MapLayerPolygon)


@skipUnless(HAS_GIS, "the demo is geometry; the command refuses a GIS-off host")
class IngressLocationsTests(TestCase):
    def setUp(self):
        get_user_model().objects.create_superuser("admin", "a@example.org", "pw")
        self.out = StringIO()

    def counts(self):
        return {model.__name__: model.objects.count() for model in MODELS}

    def test_realistic_mode_seeds_no_map_items(self):
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


class RegulatedDomainTests(TestCase):
    """The one map domain every platform has; plain columns, so no GIS needed."""

    def setUp(self):
        self.out = StringIO()

    def test_realistic_mode_seeds_it_once_keeping_nothing(self):
        call_command("ingress_locations", mode="realistic", stdout=self.out)
        domain = MapDomain.objects.get()
        self.assertEqual((domain.slug, domain.name, domain.description),
                         ("regulated_domain", "regulated_domain",
                          "Map items a superuser may keep to clearances."))
        self.assertFalse(domain.clearance_rows.exists())
        self.assertEqual(sum(kind.count(domain) for kind in MapDomainKind.all()), 0)
        self.assertIn("Created the map domain 'regulated_domain'.", self.out.getvalue())

    def test_a_rerun_keeps_a_superusers_edits(self):
        from toto.locations.models import MapDomainClearance
        from toto.socialhub.models import Clearance

        call_command("ingress_locations", mode="realistic", stdout=self.out)
        domain = MapDomain.objects.get(slug="regulated_domain")
        domain.description = "Ours now"
        domain.save()
        MapDomainClearance.objects.create(
            domain=domain, clearance=Clearance.objects.create(name="internal", slug="internal"))
        call_command("ingress_locations", mode="realistic", stdout=self.out)
        domain = MapDomain.objects.get()
        self.assertEqual(domain.description, "Ours now")
        self.assertEqual(domain.clearance_rows.count(), 1)
        self.assertIn("Kept the map domain 'regulated_domain'.", self.out.getvalue())

    @skipUnless(HAS_GIS, "full mode seeds geometry")
    def test_full_mode_seeds_it_too(self):
        get_user_model().objects.create_superuser("admin", "a@example.org", "pw")
        call_command("ingress_locations", mode="full", stdout=self.out)
        self.assertEqual(list(MapDomain.objects.values_list("slug", flat=True)), ["regulated_domain"])

    def test_mode_none_seeds_nothing(self):
        call_command("ingress_locations", mode="none", stdout=self.out)
        self.assertFalse(MapDomain.objects.exists())
