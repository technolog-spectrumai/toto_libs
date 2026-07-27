import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.locations.models import Address, Territory, Zone
from toto.api.testutils import login_mesh_member

User = get_user_model()


class ZoneListApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        self.territory = Territory.objects.create(
            name="North",
            geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))",
        )
        Zone.objects.create(
            name="Zone Alpha",
            territory=self.territory,
            geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))",
        )
        Zone.objects.create(
            name="Zone Beta",
            territory=self.territory,
            geometry="MULTIPOLYGON(((2 2, 3 2, 3 3, 2 3, 2 2)))",
        )

    def test_list_returns_200(self):
        res = self.client.get("/locations/api/zones/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("zones", data)
        self.assertEqual(len(data["zones"]), 2)

    def test_zone_has_territory_name(self):
        res = self.client.get("/locations/api/zones/")
        zone = res.json()["zones"][0]
        self.assertEqual(zone["territory_name"], "North")

    def test_sorted_by_name(self):
        res = self.client.get("/locations/api/zones/")
        names = [z["name"] for z in res.json()["zones"]]
        self.assertEqual(names, sorted(names))


class AddressListCreateApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        self.user = User.objects.create_user(username="locuser", password="pass")
        Address.objects.create(street="Main St", building="1", locality_name="Springfield", country_name="US")
        Address.objects.create(street="Oak Ave", building="5", locality_name="Shelbyville", country_name="US")

    def test_list_returns_200(self):
        res = self.client.get("/locations/api/addresses/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("addresses", data)
        self.assertEqual(len(data["addresses"]), 2)

    def test_address_fields(self):
        res = self.client.get("/locations/api/addresses/")
        addr = res.json()["addresses"][0]
        self.assertIn("street", addr)
        self.assertIn("locality_name", addr)
        self.assertIn("display", addr)

    def test_create_unauthenticated(self):
        self.client.logout()
        res = self.client.post(
            "/locations/api/addresses/",
            json.dumps({"street": "New St", "building": "2", "locality_name": "City", "country_name": "US"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_create_authenticated(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/locations/api/addresses/",
            json.dumps({"street": "New St", "building": "2", "locality_name": "City", "country_name": "US"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["street"], "New St")
        self.assertEqual(data["country_name"], "US")


class AddressDetailApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        self.address = Address.objects.create(
            street="Elm St", building="3", locality_name="Townsville", country_name="AU"
        )

    def test_detail_found(self):
        res = self.client.get(f"/locations/api/addresses/{self.address.pk}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["street"], "Elm St")

    def test_detail_not_found(self):
        res = self.client.get("/locations/api/addresses/99999/")
        self.assertEqual(res.status_code, 404)
