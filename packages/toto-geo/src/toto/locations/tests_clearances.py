"""Map items kept to clearances through map domains (2026-09-30): on the
map, in the JSON, on their pages and in the connectors a hidden one is a
missing one — for all five kinds here, and not even its creator or owner
reads it."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.locations import access
from toto.locations.models import (
    HAS_GIS, Address, MapDomain, MapDomainClearance, MapLayer, Route, Territory, Zone,
)
from toto.locations.tests_more_clearances import keep
from toto.people.models import Person
from toto.socialhub.models import Clearance

User = get_user_model()


class LocationClearanceTestCase(TestCase):
    def setUp(self):
        if not HAS_GIS:
            self.skipTest("routes draw on geometry; the map 404s without GIS")
        from django.contrib.gis.geos import LineString, MultiLineString

        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "T", "author": "t", "publication_year": 2026})
        self.board = Clearance.objects.create(name="internal", slug="internal")
        self.creator = User.objects.create_user("creator", password="x")
        self.member = User.objects.create_user("member", password="x")
        Person.objects.create(user=self.member, display_name="M").clearances.add(self.board)
        self.stranger = User.objects.create_user("stranger", password="x")
        self.owner = User.objects.create_user("owner", password="x")
        owner_person = Person.objects.create(user=self.owner, display_name="O")
        self.root = User.objects.create_superuser("root", "r@e.com", "x")
        self.domain = MapDomain.objects.create(name="Board")
        MapDomainClearance.objects.create(domain=self.domain, clearance=self.board)
        line = MultiLineString(LineString((18.6, 54.3), (21.0, 52.2)))
        self.kept = keep(Route.objects.create(name="BoardRoute", geometry=line, created_by=self.creator),
                         self.domain)
        self.open = Route.objects.create(name="OpenRoute", geometry=line, created_by=self.creator)
        self.layer = keep(MapLayer.objects.create(name="Board layer", slug="board-layer",
                                                  owner=owner_person), self.domain)
        self.address = keep(Address.objects.create(street="Board St", locality_name="Gdańsk",
                                                   latitude=54.3, longitude=18.6,
                                                   created_by=self.creator), self.domain)
        self.territory = keep(Territory.objects.create(
            name="BoardLand", geometry="POLYGON((0 0, 1 0, 1 1, 0 1, 0 0))"), self.domain)
        self.zone = keep(Zone.objects.create(
            name="BoardZone", geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))"), self.domain)

    def names(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("locations:locations_all"))
        payload = json.loads(response.context["locations_json"])
        layers = json.loads(response.context["map_layers_json"])
        return {row["name"] for row in payload}, {row["name"] for row in layers}


class DomainItemTests(LocationClearanceTestCase):
    KEPT = {"BoardRoute", "BoardLand", "BoardZone", "Board St, Gdańsk"}

    def test_the_map_shows_each_viewer_their_items(self):
        for user in (self.stranger, self.creator, self.owner):
            items, layers = self.names(user)
            with self.subTest(user=user.username):
                self.assertIn("OpenRoute", items)
                self.assertFalse(self.KEPT & items)             # no creator's or owner's bypass
                self.assertNotIn("Board layer", layers)
        for user in (self.member, self.root):
            items, layers = self.names(user)
            with self.subTest(user=user.username):
                self.assertTrue(self.KEPT <= items)
                self.assertIn("Board layer", layers)

    def test_the_pages_answer_404(self):
        urls = [reverse("locations:location_detail", args=[kind, obj.pk]) for kind, obj in (
            ("route", self.kept), ("maplayer", self.layer), ("address", self.address),
            ("territory", self.territory), ("zone", self.zone))]
        urls += [reverse("locations:route_detail", args=[self.kept.pk]),
                 reverse("locations:address_detail", args=[self.address.pk]),
                 reverse("locations:zone_detail", args=[self.zone.pk])]
        for user in (self.stranger, self.creator, self.owner):
            self.client.force_login(user)
            for url in urls:
                with self.subTest(user=user.username, url=url):
                    self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.member)
        for url in urls[:5]:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_nobody_reads_only_the_open_ones(self):
        self.assertEqual(list(access.readable_routes(None)), [self.open])
        self.assertEqual(list(access.readable_layers(None)), [])
        self.assertEqual(list(access.readable_addresses(None)), [])
        self.assertEqual(list(access.readable_zones(None)), [])
        self.assertEqual(list(access.readable_territories(None)), [])

    def test_taking_an_item_out_of_its_kept_domain_opens_it(self):
        self.kept.domain_rows.all().delete()
        self.assertTrue(access.may_read(self.stranger, self.kept))
        self.domain.clearance_rows.all().delete()
        self.assertTrue(access.may_read(self.stranger, self.layer))
