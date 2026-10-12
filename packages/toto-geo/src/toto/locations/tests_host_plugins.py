"""What a host app adds to Locations (2026-10-12): its own tabs in the bar,
its own points as a route's ends, its own items drawn as pins.

A point is resolved by its source on the server, for the viewer: a hidden or
unknown one is "not available", the browser's coordinates never stand in for
it, and a source that fails answers nothing rather than breaking the page.
"""
import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import resolve, reverse

from toto.locations.plugins.point_plugins import LocationPointPlugin
from toto.locations.plugins.tab_plugins import LocationTabPlugin
from toto.locations.tests_routes import OSRM_OK, RouteTestCase, _Response, routing

User = get_user_model()

POINTS = {
    "truck-1": {"label": "Truck 1", "group": "vehicle", "lat": 54.40, "lng": 18.66},
    "crate-9": {"label": "Crate 9", "group": "object", "lat": 54.38, "lng": 18.72},
    "secret": {"label": "Secret", "group": "object", "lat": 54.30, "lng": 18.80},
}


def options(user):
    return [{"key": key, **point} for key, point in POINTS.items() if key != "secret"]


def resolve_point(user, key):
    return None if key == "secret" else POINTS.get(key)


class PointSourceTests(SimpleTestCase):
    def setUp(self):
        LocationPointPlugin.register("thing", options=options, resolve=resolve_point)
        self.addCleanup(LocationPointPlugin.unregister, "thing")

    def test_points_come_grouped_with_their_ids(self):
        groups = LocationPointPlugin.options(None)
        self.assertEqual([g["label"] for g in groups], ["vehicle", "object"])
        self.assertEqual(groups[0]["options"], [{"id": "thing:truck-1", "label": "Truck 1",
                                                 "lat": 54.40, "lng": 18.66}])

    def test_only_a_registered_prefix_is_a_point_and_its_source_decides(self):
        self.assertTrue(LocationPointPlugin.is_point("thing:crate-9"))
        for value in ("12", "temporary:1", "other:crate-9", "thing:", "", None):
            self.assertFalse(LocationPointPlugin.is_point(value), value)
        self.assertEqual(LocationPointPlugin.resolve(None, "thing:crate-9")["lat"], 54.38)
        self.assertIsNone(LocationPointPlugin.resolve(None, "thing:secret"))
        self.assertIsNone(LocationPointPlugin.resolve(None, "thing:nothing"))

    def test_bad_coordinates_and_failing_sources_answer_nothing(self):
        LocationPointPlugin.register("bad", options=lambda user: [{"key": "x", "lat": 91, "lng": 0},
                                                                  {"key": "y", "lat": "n/a", "lng": 0}],
                                     resolve=lambda user, key: 1 / 0)
        self.addCleanup(LocationPointPlugin.unregister, "bad")
        self.assertEqual([g["label"] for g in LocationPointPlugin.options(None)], ["vehicle", "object"])
        self.assertIsNone(LocationPointPlugin.resolve(None, "bad:x"))

    def test_a_prefix_is_a_plain_name(self):
        for prefix in ("", "a:b", "temporary", "two words"):
            with self.assertRaises(ValueError):
                LocationPointPlugin.register(prefix, options=options, resolve=resolve_point)


class RoutePointTests(RouteTestCase):
    def setUp(self):
        super().setUp()
        LocationPointPlugin.register("thing", options=options, resolve=resolve_point)
        self.addCleanup(LocationPointPlugin.unregister, "thing")

    def test_the_selects_offer_the_points_by_group(self):
        page = self.client.get(self.url)
        self.assertContains(page, '<optgroup label="vehicle">', count=2)
        self.assertContains(page, 'value="thing:crate-9"', count=2)
        self.assertNotContains(page, "thing:secret")

    def test_a_route_between_two_points_uses_their_sources_coordinates(self):
        with routing() as routed:
            page = self.client.get(self.url, {"mode": "foot", "start_address": "thing:truck-1",
                                              "end_address": "thing:crate-9",
                                              # what a browser sent is not believed
                                              "start_lat": "1", "start_lng": "1"})
        self.assertEqual(page.context["error"], "")
        asked = routed.call_args[0][0].full_url
        self.assertIn("18.66,54.4;18.72,54.38", asked)
        self.assertContains(page, 'value="Truck 1 → Crate 9"')
        self.assertContains(page, 'value="thing:truck-1" data-lat="54.4" data-lng="18.66"\n')

    def test_a_point_and_an_address_mix(self):
        with routing():
            page = self.client.get(self.url, {"mode": "car", "start_address": self.home.pk,
                                              "end_address": "thing:crate-9"})
        self.assertEqual(page.context["error"], "")
        self.assertIn("→ Crate 9", page.context["route_name"])

    def test_a_hidden_or_unknown_point_is_not_available(self):
        for value in ("thing:secret", "thing:nothing"):
            with routing() as routed:
                page = self.client.get(self.url, {"mode": "car", "start_address": value,
                                                  "end_address": self.work.pk})
            self.assertEqual(page.context["error"], "Start point is not available.")
            routed.assert_not_called()

    def test_the_api_takes_points(self):
        url = reverse("locations:api_route_search")
        with routing():
            answer = self.client.post(url, json.dumps({"mode": "car", "start_point": "thing:truck-1",
                                                       "end_point": "thing:crate-9"}),
                                      content_type="application/json")
        self.assertEqual(answer.status_code, 200, answer.content)
        self.assertEqual(answer.json()["properties"]["start"]["label"], "Truck 1")
        with routing():
            answer = self.client.post(url, json.dumps({"mode": "car", "start_point": "thing:secret",
                                                       "end_lat": 54, "end_lng": 18}),
                                      content_type="application/json")
        self.assertEqual(answer.status_code, 400)
        self.assertIn("not available", answer.json()["error"])


class TabTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ada", password="x")
        LocationTabPlugin.register(key="things", label="Things", icon="fa-location-pin",
                                   url_name="locations:zones", namespace="locations", url_names=("zones",))
        LocationTabPlugin.register(key="staff", label="Staff only", icon="fa-lock",
                                   url_name="locations:zones", visible=lambda user: user.is_staff)
        LocationTabPlugin.register(key="broken", label="Broken", icon="fa-x", url_name="nowhere:at-all")
        for key in ("things", "staff", "broken"):
            self.addCleanup(LocationTabPlugin.unregister, key)

    def request(self, path, user):
        request = RequestFactory().get(path)
        request.user = user
        request.resolver_match = resolve(path)
        return request

    def test_a_viewer_sees_the_tabs_meant_for_them_and_the_active_one_is_marked(self):
        zones = reverse("locations:zones")
        tabs = LocationTabPlugin.tabs(self.request(zones, self.user))
        self.assertEqual([t["key"] for t in tabs], ["things"])
        self.assertTrue(tabs[0]["active"])
        self.assertEqual(tabs[0]["url"], zones)
        other = LocationTabPlugin.tabs(self.request(reverse("locations:people"), self.user))
        self.assertFalse(other[0]["active"])
        self.user.is_staff = True
        self.assertEqual([t["key"] for t in LocationTabPlugin.tabs(self.request(zones, self.user))],
                         ["staff", "things"])
        from django.contrib.auth.models import AnonymousUser

        self.assertEqual(LocationTabPlugin.tabs(self.request(zones, AnonymousUser())), [])

    def test_the_bar_draws_them(self):
        from toto.locations.models import HAS_GIS

        if not HAS_GIS:
            self.skipTest("the Locations pages 404 without GIS")
        from toto.core.models import Platform

        Platform.objects.get_or_create(active=True, defaults={"site_name": "T", "author": "t",
                                                              "publication_year": 2026})
        self.client.force_login(self.user)
        page = self.client.get(reverse("locations:zones"))
        self.assertContains(page, 'data-testid="locations-tab-things"')
        self.assertNotContains(page, 'data-testid="locations-tab-staff"')
        self.assertNotContains(page, 'data-testid="locations-tab-broken"')


class PinTests(TestCase):
    def test_the_map_draws_a_pin_for_an_item_that_asks(self):
        from toto.locations.models import HAS_GIS

        if not HAS_GIS:
            self.skipTest("the Locations pages 404 without GIS")
        from toto.core.models import Platform
        from toto.locations.plugins.map_plugins import LocationMapPlugin

        Platform.objects.get_or_create(active=True, defaults={"site_name": "T", "author": "t",
                                                              "publication_year": 2026})
        user = User.objects.create_user("ada", password="x")
        self.client.force_login(user)
        item = {"type": "vehicle", "name": "Truck 1", "detail": "", "note": "", "marker": "pin",
                "geometry": {"type": "Point", "coordinates": [18.66, 54.4]}}
        with mock.patch.object(LocationMapPlugin, "_providers", [lambda request: [item]]):
            page = self.client.get(reverse("locations:locations_all"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Truck 1")
        self.assertContains(page, 'item.marker === "pin"')
