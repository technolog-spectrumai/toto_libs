"""Routes and map layers kept to clearances (2026-09-29): on the map, in the
JSON, on their pages and in the connectors a hidden one is a missing one."""

import json

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.audit.models import AuditRecord
from toto.core.models import Platform
from toto.locations import access
from toto.locations.models import HAS_GIS, MapLayer, MapLayerClearance, Route, RouteClearance
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
        self.root = User.objects.create_superuser("root", "r@e.com", "x")
        line = MultiLineString(LineString((18.6, 54.3), (21.0, 52.2)))
        self.kept = Route.objects.create(name="BoardRoute", geometry=line, created_by=self.creator)
        RouteClearance.objects.create(route=self.kept, clearance=self.board)
        self.open = Route.objects.create(name="OpenRoute", geometry=line, created_by=self.creator)
        self.layer = MapLayer.objects.create(name="Board layer", slug="board-layer")
        MapLayerClearance.objects.create(layer=self.layer, clearance=self.board)

    def names(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("locations:locations_all"))
        payload = json.loads(response.context["locations_json"])
        layers = json.loads(response.context["map_layers_json"])
        return {row["name"] for row in payload}, {row["name"] for row in layers}


class RouteAndLayerTests(LocationClearanceTestCase):
    def test_the_map_shows_each_viewer_their_routes_and_layers(self):
        routes, layers = self.names(self.stranger)
        self.assertIn("OpenRoute", routes)
        self.assertNotIn("BoardRoute", routes)
        self.assertNotIn("Board layer", layers)
        for user in (self.member, self.creator, self.root):
            routes, layers = self.names(user)
            self.assertIn("BoardRoute", routes, user)
        self.assertIn("Board layer", self.names(self.member)[1])
        self.assertIn("Board layer", self.names(self.root)[1])
        self.assertNotIn("Board layer", self.names(self.creator)[1])    # not the layer's owner

    def test_the_pages_answer_404(self):
        detail = reverse("locations:location_detail", args=["route", self.kept.pk])
        redirect = reverse("locations:route_detail", args=[self.kept.pk])
        layer = reverse("locations:location_detail", args=["maplayer", self.layer.pk])
        self.client.force_login(self.stranger)
        for url in (detail, redirect, layer):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertEqual(self.client.get(layer).status_code, 200)

    def test_nobody_reads_only_the_open_ones(self):
        self.assertEqual(list(access.readable_routes(None)), [self.open])
        self.assertEqual(list(access.readable_layers(None)), [])

    def test_the_creator_chooses_the_clearances_and_it_is_audited(self):
        Person.objects.create(user=self.creator, display_name="C").clearances.add(self.board)
        url = reverse("locations:clearances_save", args=["route", self.open.pk])
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.post(url, {"clearance": [self.board.pk]}).status_code, 403)
        self.client.force_login(self.creator)
        self.client.post(url, {"clearance": [self.board.pk]})
        self.assertFalse(access.may_read(self.stranger, self.open))
        record = AuditRecord.objects.filter(action="LOCATIONS.ROUTE.CLEARANCES_CHANGED").get()
        self.assertEqual(record.metadata["after"], ["internal"])
        self.client.post(url, {})
        self.assertTrue(access.may_read(self.stranger, self.open))

    def test_the_detail_page_shows_the_section_to_whoever_manages(self):
        detail = reverse("locations:location_detail", args=["route", self.kept.pk])
        self.client.force_login(self.member)
        body = self.client.get(detail).content.decode()
        self.assertIn('data-testid="location-clearances"', body)
        self.assertNotIn(reverse("locations:clearances_save", args=["route", self.kept.pk]), body)
        self.client.force_login(self.root)
        self.assertIn(reverse("locations:clearances_save", args=["route", self.kept.pk]),
                      self.client.get(detail).content.decode())
