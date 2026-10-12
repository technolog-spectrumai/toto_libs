"""The Zones tab, and a map without layers (2026-10-12). The owner: "add
zones tab to locations and remove layers".

    manage.py test toto.locations.tests_zones_tab
"""

from django.contrib.auth import get_user_model
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform

from .models import Territory, Zone

SQUARE = MultiPolygon(Polygon(((21.0, 52.2), (21.1, 52.2), (21.1, 52.3), (21.0, 52.3), (21.0, 52.2))), srid=4326)


class ZonesTabTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.user = get_user_model().objects.create_user("ada", password="pw")
        cls.land = Territory.objects.create(name="Mazovia")
        cls.north = Zone.objects.create(name="North", territory=cls.land, geometry=SQUARE)
        cls.south = Zone.objects.create(name="South", territory=cls.land, geometry=SQUARE)

    def setUp(self):
        self.client.force_login(self.user)

    def test_the_tab_lists_the_zones_with_their_territory_and_page(self):
        response = self.client.get(reverse("locations:zones"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["name"] for row in response.context["zones"]], ["North", "South"])
        body = response.content.decode()
        self.assertEqual(body.count('data-testid="locations-zone-row"'), 2)
        self.assertIn("Mazovia", body)
        self.assertIn(reverse("locations:zone_detail", args=[self.north.pk]), body)
        self.assertIn('data-testid="locations-tab-zones"', body)

    def test_every_page_of_locations_has_the_tab(self):
        body = self.client.get(reverse("locations:people")).content.decode()
        self.assertIn(reverse("locations:zones"), body)

    def test_signed_out_there_is_no_list(self):
        self.client.logout()
        self.assertIn(self.client.get(reverse("locations:zones")).status_code, (302, 403))

    def test_the_map_has_layers_unless_the_host_says_no(self):
        body = self.client.get(reverse("locations:locations_all")).content.decode()
        self.assertIn("Import layer", body)
        self.assertIn("layersOpen = !layersOpen", body)

    @override_settings(LOCATIONS_LAYERS=False)
    def test_a_host_without_layers_draws_none_and_takes_none(self):
        response = self.client.get(reverse("locations:locations_all"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertNotIn("Import layer", body)
        self.assertNotIn("layersOpen = !layersOpen", body)
        self.assertEqual(response.context["map_layers"], [])
        self.assertEqual(self.client.post(reverse("locations:api_import_layer")).status_code, 404)
