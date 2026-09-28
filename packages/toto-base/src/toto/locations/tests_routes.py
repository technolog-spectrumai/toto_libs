"""Route search by typed place names: "Gdansk" to "Warsaw" (2026-09-28).

Each typed name is one charged place lookup. These pin who pays and when:
the batch is checked before the first name is asked, a name is asked once
(the page carries the answer, so a reload or a re-submit with the same text
is free), a GET never asks, and a refusal lands in the page's error box.
Nothing here reaches the network: the lookups are patched at
`toto.locations.geocoding` (or at the provider's `urlopen`, where the charge
itself is under test) and the router at `toto.locations.views.urlopen`.
"""

import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.locations import geocoding
from toto.locations.models import HAS_GIS, Address, LocationsUsageEvent
from toto.locations.tests_geocoding import GDANSK, answering, unthrottled

User = get_user_model()

ON = {"enabled": True, "user_agent": "toto-test/1.0"}
OFF = {"enabled": False}

MATCHES = {
    "Gdansk": {"label": "Gdańsk, województwo pomorskie, Polska", "name": "Gdańsk",
               "lat": 54.352, "lng": 18.6466, "type": "city"},
    "Warsaw": {"label": "Warszawa, województwo mazowieckie, Polska", "name": "Warszawa",
               "lat": 52.2297, "lng": 21.0122, "type": "city"},
    "Krakow": {"label": "Kraków, województwo małopolskie, Polska", "name": "Kraków",
               "lat": 50.0619, "lng": 19.9368, "type": "city"},
}

OSRM_OK = {"code": "Ok", "routes": [{
    "distance": 340000, "duration": 14400,
    "geometry": {"type": "LineString",
                 "coordinates": [[18.6466, 54.352], [21.0122, 52.2297]]},
}]}


class _Response:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def routing():
    """The router, answering one straight line whatever it is asked."""
    return mock.patch("toto.locations.views.urlopen", return_value=_Response(OSRM_OK))


def looking_up():
    """first_match from MATCHES (None for anything else), and check_affordable
    as a mock; both hang off one parent so their order can be asserted."""
    calls = mock.Mock()
    calls.first_match.side_effect = lambda user, name: MATCHES.get(name)
    patches = (mock.patch.object(geocoding, "first_match", calls.first_match),
               mock.patch.object(geocoding, "check_affordable", calls.check_affordable))
    return calls, patches


def query(location):
    """The redirect's query string, one value per key, blanks kept (a blank
    address is "not the saved one")."""
    params = parse_qs(urlsplit(location).query, keep_blank_values=True)
    return {key: values[0] for key, values in params.items()}


@override_settings(LOCATIONS_GEOCODING=ON)
class RouteTestCase(TestCase):
    def setUp(self):
        if not HAS_GIS:
            self.skipTest("route search draws on geometry; it 404s without GIS")
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "T", "author": "t", "publication_year": 2026})
        cache.clear()
        unthrottled(self)
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)
        self.url = reverse("locations:route_search")
        # Two saved addresses, so the selects have a default each: a typed
        # name has something to win over.
        self.home = Address.objects.create(street="Długa", building="1", locality_name="Gdańsk",
                                           latitude=54.349, longitude=18.653)
        self.work = Address.objects.create(street="Nowy Świat", building="2",
                                           locality_name="Warszawa",
                                           latitude=52.232, longitude=21.019)
        self.calls, patches = looking_up()
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def search(self, **fields):
        data = {"mode": "car", "start_address": self.home.pk, "end_address": self.work.pk}
        data.update(fields)
        return self.client.post(self.url, data)


class TypedPlaceTests(RouteTestCase):
    def test_both_names_are_affordable_first_then_asked_once_each(self):
        response = self.search(start_query="Gdansk", end_query=" Warsaw ")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.calls.mock_calls, [
            mock.call.check_affordable(self.user, 2),
            mock.call.first_match(self.user, "Gdansk"),
            mock.call.first_match(self.user, "Warsaw"),
        ])

        params = query(response["Location"])
        self.assertEqual((params["start_lat"], params["start_lng"]), ("54.352", "18.6466"))
        self.assertEqual((params["start_resolved"], params["end_resolved"]), ("Gdansk", "Warsaw"))
        self.assertEqual(params["end_label"], MATCHES["Warsaw"]["label"])

    def test_the_redirect_draws_the_route_and_names_both_ends(self):
        target = self.search(start_query="Gdansk", end_query="Warsaw")["Location"]
        with routing() as routed:
            page = self.client.get(target)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.context["error"], "")
        self.assertEqual(page.context["route"]["properties"]["distance_km"], 340.0)
        self.assertIn("18.6466,54.352;21.0122,52.2297", routed.call_args.args[0].full_url)
        self.assertContains(page, MATCHES["Gdansk"]["label"])
        self.assertContains(page, 'data-testid="end-place"')
        self.assertContains(page, 'value="Gdansk → Warsaw"')
        self.assertEqual(page.context["point_labels"]["start"], MATCHES["Gdansk"]["label"])

    def test_a_reload_asks_nobody(self):
        target = self.search(start_query="Gdansk", end_query="Warsaw")["Location"]
        self.calls.reset_mock()
        with routing():
            self.client.get(target)
            self.client.get(target)
        self.assertEqual(self.calls.mock_calls, [])

    def test_resubmitting_the_same_text_asks_nobody(self):
        target = self.search(start_query="Gdansk", end_query="Warsaw")["Location"]
        self.calls.reset_mock()
        again = query(target)
        again["mode"] = "bicycle"
        response = self.client.post(self.url, again)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.calls.mock_calls, [])
        self.assertEqual(query(response["Location"])["mode"], "bicycle")

    def test_changing_one_name_asks_for_that_one_only(self):
        again = query(self.search(start_query="Gdansk", end_query="Warsaw")["Location"])
        self.calls.reset_mock()
        again["end_query"] = "Krakow"
        params = query(self.client.post(self.url, again)["Location"])
        self.assertEqual(self.calls.mock_calls, [
            mock.call.check_affordable(self.user, 1),
            mock.call.first_match(self.user, "Krakow"),
        ])
        self.assertEqual((params["start_lat"], params["end_lat"]), ("54.352", "50.0619"))

    def test_the_typed_name_wins_over_the_saved_address(self):
        target = self.search(start_query="Gdansk")["Location"]
        params = query(target)
        self.assertEqual(params["start_address"], "")
        self.assertEqual(params["end_address"], str(self.work.pk))
        with routing() as routed:
            self.client.get(target)
        self.assertIn("18.6466,54.352;21.019,52.232", routed.call_args.args[0].full_url)

    def test_a_cleared_box_drops_its_markers(self):
        again = query(self.search(start_query="Gdansk")["Location"])
        again["start_query"] = ""
        params = query(self.client.post(self.url, again)["Location"])
        self.assertEqual((params["start_resolved"], params["start_label"]), ("", ""))

    def test_no_place_found_is_said_and_what_resolved_is_kept(self):
        response = self.search(start_query="Atlantis", end_query="Warsaw")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], "No place found for 'Atlantis'.")
        self.assertEqual(len(self.calls.first_match.mock_calls), 2)
        # Warsaw was paid for: the page keeps its answer, so fixing Atlantis
        # does not ask for Warsaw again.
        self.assertContains(response, 'name="end_resolved" value="Warsaw"')
        self.assertEqual(response.context["form"]["end_lat"], "52.2297")

    def test_a_billing_refusal_is_the_error_and_nothing_is_asked(self):
        from toto.quota.charge import InsufficientFunds

        refusal = InsufficientFunds("RED", 1, 0)
        self.calls.check_affordable.side_effect = refusal
        response = self.search(start_query="Gdansk", end_query="Warsaw")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], str(refusal))
        self.calls.first_match.assert_not_called()

    def test_a_lookup_refusal_keeps_what_resolved_before_it(self):
        self.calls.first_match.side_effect = [
            MATCHES["Gdansk"],
            geocoding.GeocodingError("Place lookup is busy. Try again in a moment.", 429),
        ]
        response = self.search(start_query="Gdansk", end_query="Warsaw")
        self.assertEqual(response.context["error"], "Place lookup is busy. Try again in a moment.")
        self.assertContains(response, 'name="start_resolved" value="Gdansk"')

    def test_a_broken_free_end_is_refused_before_the_other_is_charged(self):
        response = self.search(start_query="Gdansk", end_address="", end_lat="north", end_lng="1")
        self.assertEqual(response.context["error"], "End latitude must be a number.")
        self.assertEqual(self.calls.mock_calls, [])

    def test_a_bad_second_name_is_refused_before_the_first_is_charged(self):
        response = self.search(start_query="Gdansk", end_query="W")
        self.assertEqual(response.context["error"], "Type at least 2 characters to search.")
        self.assertEqual(self.calls.mock_calls, [])

    def test_a_get_never_looks_anything_up(self):
        """An old link or a hand-made URL naming a place spends nothing."""
        with routing() as routed:
            page = self.client.get(self.url, {"mode": "car", "start_query": "Gdansk",
                                              "end_address": self.work.pk})
        self.assertEqual(self.calls.mock_calls, [])
        routed.assert_not_called()
        self.assertIn("has not been looked up yet", page.context["error"])

    def test_the_lookup_needs_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = client.post(self.url, {"mode": "car", "start_query": "Gdansk"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls.mock_calls, [])


class RouteWithoutNamesTests(RouteTestCase):
    def test_saved_addresses_still_route_with_a_get_and_no_lookup(self):
        with routing() as routed:
            page = self.client.get(self.url, {"mode": "foot", "start_address": self.home.pk,
                                              "end_address": self.work.pk})
        self.assertEqual(page.context["error"], "")
        self.assertIsNotNone(page.context["route"])
        self.assertEqual(self.calls.mock_calls, [])
        self.assertContains(page, "value=\"Długa 1, Gdańsk → Nowy Świat 2, Warszawa\"")
        routed.assert_called_once()

    def test_the_router_is_told_who_is_asking(self):
        """OpenStreetMap's services ask for an identifying User-Agent; urllib's
        default is the anonymous traffic they throttle first."""
        with routing() as routed:
            self.client.get(self.url, {"mode": "car", "start_address": self.home.pk,
                                       "end_address": self.work.pk})
        self.assertEqual(routed.call_args.args[0].get_header("User-agent"), "toto-test/1.0")

    def test_the_page_offers_typed_places_with_their_price_note(self):
        page = self.client.get(self.url)
        self.assertContains(page, 'name="start_query"')
        self.assertContains(page, 'name="end_query"')
        self.assertContains(page, 'data-testid="route-lookup-price"')
        # The CSRF field is there for the POST, and off for the free GET.
        self.assertContains(page, 'name="csrfmiddlewaretoken"')
        self.assertContains(page, "form.elements.csrfmiddlewaretoken.disabled = !pending")

    @override_settings(LOCATIONS_GEOCODING=OFF)
    def test_a_host_without_geocoding_offers_no_names(self):
        page = self.client.get(self.url)
        self.assertNotContains(page, 'name="start_query"')
        self.assertNotContains(page, 'data-testid="route-lookup-price"')

    def test_an_unknown_mode_is_an_error_not_a_crash(self):
        with routing():
            page = self.client.get(self.url, {"mode": "hovercraft"})
        self.assertEqual(page.status_code, 200)
        self.assertIn("Mode must be", page.context["error"])


@override_settings(LOCATIONS_GEOCODING=ON)
class ChargedRouteTests(TestCase):
    """The real service behind the page: two typed places, two lookups."""

    def setUp(self):
        if not HAS_GIS:
            self.skipTest("route search draws on geometry; it 404s without GIS")
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "T", "author": "t", "publication_year": 2026})
        cache.clear()
        unthrottled(self)
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def test_each_typed_place_is_one_lookup_and_the_reload_is_free(self):
        url = reverse("locations:route_search")
        with answering(GDANSK):
            response = self.client.post(url, {"mode": "car", "start_query": "Gdansk",
                                              "end_query": "Gdańsk centrum"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LocationsUsageEvent.objects.filter(user=self.user).count(), 2)
        with routing(), mock.patch("toto.locations.geocode.urlopen") as asked:
            self.client.get(response["Location"])
        asked.assert_not_called()
        self.assertEqual(LocationsUsageEvent.objects.filter(user=self.user).count(), 2)


class RouteSearchApiTests(RouteTestCase):
    def post(self, payload, **extra):
        return self.client.post(reverse("locations:api_route_search"), json.dumps(payload),
                                content_type="application/json", **extra)

    def test_typed_places_route_and_are_named_in_the_answer(self):
        with routing():
            response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 200)
        properties = response.json()["properties"]
        self.assertEqual(properties["start"]["label"], MATCHES["Gdansk"]["label"])
        self.assertEqual(properties["end"]["lat"], 52.2297)
        self.assertEqual(self.calls.mock_calls[0], mock.call.check_affordable(self.user, 2))

    def test_coordinates_and_one_name_is_one_lookup(self):
        with routing() as routed:
            response = self.post({"mode": "car", "start_lat": 54.0, "start_lng": 18.0,
                                  "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls.mock_calls, [
            mock.call.check_affordable(self.user, 1),
            mock.call.first_match(self.user, "Warsaw"),
        ])
        self.assertIn("18.0,54.0;21.0122,52.2297", routed.call_args.args[0].full_url)
        self.assertEqual(response.json()["properties"]["start"]["label"], "")

    def test_a_malformed_end_is_refused_before_any_lookup(self):
        response = self.post({"mode": "car", "start_lat": "north", "start_lng": 1,
                              "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls.mock_calls, [])

    def test_no_place_found_stops_before_the_next_name(self):
        response = self.post({"mode": "car", "start_query": "Atlantis", "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "No place found for 'Atlantis'."})
        self.assertEqual(len(self.calls.first_match.mock_calls), 1)

    def test_a_refusal_answers_with_its_own_status(self):
        from toto.quota.charge import InsufficientFunds

        self.calls.check_affordable.side_effect = InsufficientFunds("RED", 1, 0)
        response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 402)
        self.assertIn("error", response.json())
        self.calls.first_match.assert_not_called()

    def test_a_bad_second_name_is_refused_before_any_lookup(self):
        response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "W"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Type at least 2 characters to search."})
        self.assertEqual(self.calls.mock_calls, [])

    def test_a_refusal_after_a_charge_hands_back_the_end_paid_for(self):
        """Gdansk was charged: the answer carries it, so a retry sends its
        coordinates instead of paying for the name again."""
        self.calls.first_match.side_effect = [
            MATCHES["Gdansk"],
            geocoding.GeocodingError("Place lookup is not answering.", 503),
        ]
        response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {
            "error": "Place lookup is not answering.",
            "start": {"lat": 54.352, "lng": 18.6466, "label": MATCHES["Gdansk"]["label"]},
        })

    def test_a_route_the_router_refuses_still_hands_back_both_ends(self):
        with mock.patch("toto.locations.views.fetch_traversable_route",
                        side_effect=ValueError("No route found.")):
            response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(set(response.json()), {"error", "start", "end"})

    def test_a_mode_that_is_not_a_word_is_refused_not_a_crash(self):
        response = self.post({"mode": ["car"], "start_lat": 54.0, "start_lng": 18.0,
                              "end_lat": 52.0, "end_lng": 21.0})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Invalid mode."})

    def test_a_throttle_says_when_to_come_back(self):
        self.calls.first_match.side_effect = geocoding.GeocodingError(
            "Place lookup is busy.", 429, retry_after=1)
        response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "Warsaw"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "1")

    def test_a_cookie_write_from_another_site_is_refused(self):
        """csrf_exempt for the desktop clients' Bearer token; a browser's
        cookie from another site must not spend the member's mana."""
        response = self.post({"mode": "car", "start_query": "Gdansk", "end_query": "Warsaw"},
                             HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls.mock_calls, [])

    def test_coordinates_alone_still_work(self):
        with routing():
            response = self.post({"mode": "foot", "start_lat": 54.0, "start_lng": 18.0,
                                  "end_lat": 52.0, "end_lng": 21.0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls.mock_calls, [])
