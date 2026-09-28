"""Server-side geocoding: one lookup charged per answer, nothing for a refusal.

2026-09-28. The provider is never reached from here: `urlopen` is patched
with a context manager whose read() returns bytes, and every test clears the
cache, because the cache and both throttles live in it.
"""

import http.client
import importlib
import json
from decimal import Decimal
from unittest import mock
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import clear_url_caches, reverse

from toto.core import ratelimit
from toto.core.models import Platform
from toto.locations import geocode, geocoding
from toto.locations.geocoding import GeocodingError
from toto.locations.models import HAS_GIS, LocationsQuotaPolicy, LocationsUsageEvent

User = get_user_model()

ON = {"enabled": True}
OFF = {"enabled": False}
URLOPEN = "toto.locations.geocode.urlopen"

GDANSK = [{
    "display_name": "Gdańsk, województwo pomorskie, Polska",
    "name": "Gdańsk", "lat": "54.3520", "lon": "18.6466", "type": "city",
    "address": {"city": "Gdańsk", "state": "województwo pomorskie", "country_code": "pl"},
}]
DLUGA = {
    "display_name": "Długa 1, Gdańsk, Polska",
    "address": {"road": "Długa", "house_number": "1", "city": "Gdańsk",
                "state": "województwo pomorskie", "country_code": "pl"},
}
SEA = {"error": "Unable to geocode"}


class _Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.body


def answering(payload):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return mock.patch(URLOPEN, return_value=_Response(body))


def failing(exc):
    return mock.patch(URLOPEN, side_effect=exc)


def unthrottled(test):
    """One provider call a second is the production pace; a test asking twice
    in a row should not sleep for it. The throttle tests pin it themselves."""
    patcher = mock.patch.object(geocoding, "PROVIDER_LIMIT", 1000)
    patcher.start()
    test.addCleanup(patcher.stop)


PROVIDER_FAILURES = (
    ("http error", HTTPError("https://geo", 429, "Too Many Requests", {}, None)),
    ("url error", URLError("name or service not known")),
    ("reset", ConnectionResetError("reset by peer")),
    ("timeout", TimeoutError("timed out")),
    ("truncated", http.client.IncompleteRead(b"[{")),
)
BAD_BODIES = (
    ("not json", b"<html>busy</html>"),
    ("not utf-8", b"\xff\xfe\xfa"),
)


# ---------------------------------------------------------------------------
# The provider adapter (toto.locations.geocode)
# ---------------------------------------------------------------------------

@override_settings(LOCATIONS_GEOCODING=ON)
class NormaliserTests(SimpleTestCase):
    def test_forward_results_carry_float_coordinates(self):
        with answering(GDANSK):
            results = geocode.forward_geocode_locations("Gdansk")
        self.assertEqual(results[0]["lat"], 54.352)
        self.assertEqual(results[0]["lng"], 18.6466)
        self.assertEqual(results[0]["country_name"], "PL")
        self.assertEqual(results[0]["locality_name"], "Gdańsk")

    def test_items_without_usable_coordinates_are_dropped(self):
        payload = GDANSK + [{"name": "Nowhere", "lat": "north", "lon": "1"}, "junk",
                            {"name": "Inf", "lat": "inf", "lon": "1"}]
        with answering(payload):
            results = geocode.forward_geocode_locations("Gdansk")
        self.assertEqual([r["name"] for r in results], ["Gdańsk"])

    def test_reverse_carries_the_display_name_as_label(self):
        with answering(DLUGA):
            result = geocode.reverse_geocode_address(54.35, 18.65)
        self.assertEqual(result["label"], "Długa 1, Gdańsk, Polska")
        self.assertEqual((result["street"], result["building"]), ("Długa", "1"))

    def test_the_fail_silent_reverse_keeps_its_placeholder(self):
        """Existing callers (the address form) still get "Map point"."""
        with answering(SEA):
            result = geocode.reverse_geocode_address(0, 0)
        self.assertEqual(result["building"], "Map point")

    def test_describe_point_tells_no_address_from_an_address(self):
        with answering(SEA):
            nothing = geocode.describe_point(0.0, 0.0)
        self.assertFalse(nothing["found"])
        self.assertEqual(set(nothing["fields"].values()), {""})
        with answering({"display_name": "Somewhere", "address": {"road": "A"}}):
            found = geocode.describe_point(1.0, 1.0)
        self.assertTrue(found["found"])
        self.assertEqual(found["fields"]["building"], "",
                         "a form field must not be filled with a placeholder")

    def test_fail_silent_helpers_swallow_every_provider_failure_and_log_it(self):
        for name, exc in PROVIDER_FAILURES:
            with self.subTest(name), failing(exc), \
                    self.assertLogs("toto.locations.geocode", "WARNING") as logs:
                self.assertEqual(geocode.forward_geocode_locations("Gdańsk"), [])
                self.assertEqual(geocode.reverse_geocode_address(54.3, 18.6), {})
            self.assertNotIn("Gdańsk", "\n".join(logs.output),
                             "where a member looked stays out of the log")

    def test_fail_silent_helpers_swallow_odd_bodies(self):
        for name, body in BAD_BODIES + (("object for search", b'{"a": 1}'),):
            with self.subTest(name), answering(body), self.assertLogs("toto.locations.geocode", "WARNING"):
                self.assertEqual(geocode.forward_geocode_locations("Gdańsk"), [])
        with answering(b"[1, 2]"), self.assertLogs("toto.locations.geocode", "WARNING"):
            self.assertEqual(geocode.reverse_geocode_address(54.3, 18.6), {})

    def test_the_raising_variants_raise(self):
        for name, exc in PROVIDER_FAILURES:
            with self.subTest(name), failing(exc), self.assertLogs("toto.locations.geocode", "WARNING"):
                with self.assertRaises(geocode.GeocodingUnavailable):
                    geocode.search_places("Gdańsk")
                with self.assertRaises(geocode.GeocodingUnavailable):
                    geocode.describe_point(54.3, 18.6)
        with answering(b'{"not": "a list"}'), self.assertLogs("toto.locations.geocode", "WARNING"):
            with self.assertRaises(geocode.GeocodingUnavailable):
                geocode.search_places("Gdańsk")


# ---------------------------------------------------------------------------
# The charged service (toto.locations.geocoding)
# ---------------------------------------------------------------------------

@override_settings(LOCATIONS_GEOCODING=ON)
class ServiceTestCase(TestCase):
    def setUp(self):
        cache.clear()
        unthrottled(self)
        self.user = User.objects.create_user("ada", password="x")

    def events(self):
        return LocationsUsageEvent.objects.filter(user=self.user)

    def assertRefused(self, status, call):
        with self.assertRaises(GeocodingError) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        self.assertTrue(str(caught.exception))
        return caught.exception


class ChargingTests(ServiceTestCase):
    def test_a_search_is_one_lookup(self):
        with answering(GDANSK):
            results = geocoding.search(self.user, "Gdansk")
        self.assertEqual(results[0]["lat"], 54.352)
        event = self.events().get()
        self.assertEqual((event.metric_code, event.quantity, event.unit),
                         ("locations.geocode", Decimal("1"), "lookup"))
        self.assertTrue(event.idempotency_key.startswith("locations.geocode:"))
        self.assertEqual(event.source_label, "Place search")

    def test_a_cache_hit_asks_nobody_and_is_still_one_lookup(self):
        with answering(GDANSK):
            first = geocoding.search_answer(self.user, "Gdansk")
        with mock.patch(URLOPEN) as opened:
            second = geocoding.search_answer(self.user, "  gdansk ")
        opened.assert_not_called()
        self.assertEqual((first.cached, second.cached), (False, True))
        self.assertEqual(second.value, first.value)
        self.assertEqual(self.events().count(), 2)

    def test_an_empty_search_is_an_answer(self):
        with answering([]):
            self.assertEqual(geocoding.search(self.user, "Qwxzy"), [])
        self.assertEqual(self.events().count(), 1)

    def test_reverse_answers_the_rounded_point_and_is_one_lookup(self):
        with answering(DLUGA):
            result = geocoding.reverse(self.user, "54.3520001", 18.6466049)
        self.assertEqual((result["lat"], result["lng"]), (54.352, 18.6466))
        self.assertEqual(result["label"], "Długa 1, Gdańsk, Polska")
        self.assertEqual(result["fields"]["street"], "Długa")
        self.assertTrue(result["found"])
        self.assertEqual(self.events().get().source_label, "Address lookup")

    def test_a_point_with_no_address_is_charged_too(self):
        with answering(SEA):
            result = geocoding.reverse(self.user, 0, -30)
        self.assertFalse(result["found"])
        self.assertEqual(self.events().count(), 1)

    def test_first_match_is_the_best_hit_for_one_lookup(self):
        with answering(GDANSK * 2):
            self.assertEqual(geocoding.first_match(self.user, "Gdansk")["name"], "Gdańsk")
        with answering([]):
            self.assertIsNone(geocoding.first_match(self.user, "Qwxzy"))
        self.assertEqual(self.events().count(), 2)


class RefusalTests(ServiceTestCase):
    def test_a_provider_failure_is_503_logged_uncharged_and_not_cached(self):
        for name, exc in PROVIDER_FAILURES:
            cache.clear()
            with self.subTest(name), failing(exc), self.assertLogs("toto.locations.geocode", "WARNING"):
                self.assertRefused(503, lambda: geocoding.search(self.user, "Gdansk"))
                self.assertRefused(503, lambda: geocoding.reverse(self.user, 54.3, 18.6))
        for name, body in BAD_BODIES + (("object for search", b'{"a": 1}'),):
            cache.clear()
            with self.subTest(name), answering(body), self.assertLogs("toto.locations.geocode", "WARNING"):
                self.assertRefused(503, lambda: geocoding.search(self.user, "Gdansk"))
        self.assertFalse(self.events().exists())
        cache.clear()
        with answering(GDANSK) as opened:
            geocoding.search(self.user, "Gdansk")
        opened.assert_called_once()

    def test_bad_input_is_400_and_asks_nobody(self):
        with mock.patch(URLOPEN) as opened:
            for query in ("", " a ", "x" * 201, None):
                with self.subTest(query=query):
                    self.assertRefused(400, lambda: geocoding.search(self.user, query))
            for lat, lng in (("91", "0"), ("0", "181"), ("-90.5", "0"), ("abc", "1"),
                             ("nan", "1"), ("1", "inf"), (None, None), ("", "")):
                with self.subTest(lat=lat, lng=lng):
                    self.assertRefused(400, lambda: geocoding.reverse(self.user, lat, lng))
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_the_longest_query_allowed_is_asked(self):
        with answering([]) as opened:
            geocoding.search(self.user, "x" * 200)
        opened.assert_called_once()

    @override_settings(LOCATIONS_GEOCODING=OFF)
    def test_a_host_with_geocoding_off_answers_404(self):
        with mock.patch(URLOPEN) as opened:
            self.assertRefused(404, lambda: geocoding.search(self.user, "Gdansk"))
            self.assertRefused(404, lambda: geocoding.reverse(self.user, 1, 1))
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_nobody_anonymous_looks_anything_up(self):
        with mock.patch(URLOPEN) as opened:
            for user in (None, AnonymousUser()):
                self.assertRefused(403, lambda: geocoding.search(user, "Gdansk"))
                self.assertRefused(403, lambda: geocoding.check_affordable(user, 2))
        opened.assert_not_called()

    def test_the_per_member_limit_is_429_and_uncharged(self):
        with mock.patch.object(geocoding, "USER_LIMIT", 1), answering(GDANSK):
            geocoding.search(self.user, "Gdansk")
            refusal = self.assertRefused(429, lambda: geocoding.search(self.user, "Gdansk"))
        self.assertGreaterEqual(refusal.retry_after, 1)
        self.assertEqual(self.events().count(), 1)
        other = User.objects.create_user("bob", password="x")
        with mock.patch.object(geocoding, "USER_LIMIT", 1), mock.patch(URLOPEN) as opened:
            geocoding.search(other, "Gdansk")
        opened.assert_not_called()

    def test_the_provider_throttle_is_429_and_uncharged(self):
        with mock.patch.object(geocoding, "PROVIDER_LIMIT", 0), \
                mock.patch.object(geocoding, "PROVIDER_WAIT", 0), mock.patch(URLOPEN) as opened:
            self.assertRefused(429, lambda: geocoding.search(self.user, "Gdansk"))
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_the_provider_throttle_waits_for_the_next_second(self):
        """Route search resolves two names back to back; the second must wait
        for the provider's next free second, not be refused after the first
        was charged."""
        real_hit, refused = ratelimit.hit, []

        def hit(key, **kwargs):
            if key == geocoding.PROVIDER_KEY and not refused:
                refused.append(key)
                return ratelimit.Hit(False, 0, 1)
            return real_hit(key, **kwargs)

        with mock.patch("toto.locations.geocoding.ratelimit.hit", side_effect=hit), \
                mock.patch("toto.locations.geocoding.time.sleep") as slept, answering(GDANSK):
            self.assertEqual(len(geocoding.search(self.user, "Gdansk")), 1)
        slept.assert_called_once()
        self.assertLessEqual(slept.call_args.args[0], 1.01 + 1e-9)
        self.assertEqual(self.events().count(), 1)

    def test_a_cache_hit_does_not_touch_the_provider_throttle(self):
        with answering(GDANSK):
            geocoding.search(self.user, "Gdansk")
        with mock.patch.object(geocoding, "PROVIDER_LIMIT", 0), \
                mock.patch.object(geocoding, "PROVIDER_WAIT", 0):
            self.assertTrue(geocoding.search_answer(self.user, "Gdansk").cached)


class BillingTests(ServiceTestCase):
    def test_insufficient_mana_refuses_before_the_provider_and_records_nothing(self):
        from toto.quota.charge import InsufficientFunds

        with mock.patch("toto.locations.billing.check_funds",
                        side_effect=InsufficientFunds("RED", 1, 0)), mock.patch(URLOPEN) as opened:
            with self.assertRaises(InsufficientFunds) as caught:
                geocoding.search(self.user, "Gdansk")
        self.assertEqual(caught.exception.status_code, 402)
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_the_daily_cap_refuses_the_lookup_after_the_last(self):
        from toto.quota.api import QuotaExceeded

        LocationsQuotaPolicy.objects.create(metric_code="locations.geocode", limit=1, unit="lookup")
        with answering(GDANSK):
            geocoding.search(self.user, "Gdansk")
        with mock.patch(URLOPEN) as opened, self.assertRaises(QuotaExceeded) as caught:
            geocoding.search(self.user, "Warsaw")
        self.assertEqual(caught.exception.status_code, 429)
        opened.assert_not_called()
        self.assertEqual(self.events().count(), 1)

    def test_check_affordable_refuses_a_pair_the_cap_cannot_cover(self):
        from toto.quota.api import QuotaExceeded

        LocationsQuotaPolicy.objects.create(metric_code="locations.geocode", limit=1, unit="lookup")
        with self.assertRaises(QuotaExceeded):
            geocoding.check_affordable(self.user, 2)
        geocoding.check_affordable(self.user, 1)
        self.assertFalse(self.events().exists())

    def test_check_affordable_asks_the_ledger_for_the_whole_batch(self):
        with mock.patch("toto.locations.billing.check_funds") as funds:
            geocoding.check_affordable(self.user, 2)
        self.assertEqual(funds.call_args.args[2:], ("locations.geocode", 2))

    def test_a_charge_the_ledger_refuses_leaves_no_event(self):
        from toto.quota.charge import InsufficientFunds

        with mock.patch("toto.locations.billing.charge",
                        side_effect=InsufficientFunds("RED", 1, 0)), answering(GDANSK):
            with self.assertRaises(InsufficientFunds):
                geocoding.search(self.user, "Gdansk")
        self.assertFalse(self.events().exists())

    def test_the_ledger_never_learns_what_was_looked_up(self):
        """With a price, the ledger's own charge_user is reached with ITS
        signature (autospec) — and with nothing that says where."""
        with mock.patch("toto.locations.billing.price_for", return_value=object()), \
                mock.patch("toto.locations.billing.check_funds"), \
                mock.patch("toto.tariffs.charge.charge_user", autospec=True) as charge_user:
            with answering(GDANSK):
                geocoding.search(self.user, "Gdansk Dluga")
            with answering(DLUGA):
                geocoding.reverse(self.user, 54.35201, 18.64662)
        self.assertEqual(charge_user.call_count, 2)
        descriptions = [call.kwargs["description"] for call in charge_user.call_args_list]
        self.assertEqual(descriptions, ["Place search", "Address lookup"])
        for call in charge_user.call_args_list:
            self.assertEqual(call.args[2:4], ("locations.geocode", 1))
            self.assertEqual(call.kwargs["unit"], "lookup")
            self.assertEqual(call.kwargs["source_type"], "locations.geocode")
            text = repr(call)
            for secret in ("Gdansk", "Dluga", "Długa", "54.35", "18.64"):
                self.assertNotIn(secret, text)
        for event in self.events():
            text = repr((event.source_label, event.source_id, event.metadata,
                         event.idempotency_key))
            for secret in ("Gdansk", "Dluga", "54.35", "18.64"):
                self.assertNotIn(secret, text)

    def test_the_metric_is_declared_and_has_its_table(self):
        from toto.quota.metrics import policy_model_for, registry

        metric = registry.get("locations.geocode")
        self.assertEqual((metric.app_label, metric.unit, metric.default_limit),
                         ("locations", "lookup", 200))
        self.assertIs(policy_model_for("locations"), LocationsQuotaPolicy)

    def test_the_metric_draws_on_compute_mana(self):
        try:
            from toto.mana.colours import COLOUR_OF, PRICES
        except ImportError:
            self.skipTest("this host ships no mana")
        self.assertEqual(COLOUR_OF["locations.geocode"], "compute")
        self.assertEqual(PRICES["locations.geocode"], Decimal("0.5"))


@override_settings(LOCATIONS_GEOCODING=ON)
class PricedLookupTests(TestCase):
    """The host's own rate card, where there is one: a lookup costs 0.5
    compute mana, and an empty compute pool refuses before anybody is asked."""

    def setUp(self):
        from django.apps import apps

        if not (apps.is_installed("toto.tariffs") and apps.is_installed("toto.mana")):
            self.skipTest("this host bills nothing")
        from toto.mana.tests import fixtures

        self.fixtures = fixtures
        master = override_settings(**fixtures.MASTER)
        master.enable()
        self.addCleanup(master.disable)
        cache.clear()
        unthrottled(self)
        fixtures.economy()
        fixtures.seed_prices()
        self.user = User.objects.create_user("ada", password="x")

    def test_a_lookup_costs_half_a_compute_mana_and_names_no_place(self):
        from toto.mana import services

        before = self.fixtures.held(self.user, "compute")
        with answering(GDANSK):
            geocoding.search(self.user, "Gdansk")
        self.assertEqual(before - self.fixtures.held(self.user, "compute"), Decimal("0.5"))
        row = services.history(self.user, "compute")[0]
        self.assertEqual(row["metric_code"], "locations.geocode")
        self.assertNotIn("Gdansk", row["label"])

    def test_an_empty_compute_pool_refuses_before_the_provider(self):
        from toto.quota.charge import InsufficientFunds

        self.fixtures.spend(self.user, "compute", self.fixtures.held(self.user, "compute") - Decimal("0.2"))
        with mock.patch(URLOPEN) as opened, self.assertRaises(InsufficientFunds) as caught:
            geocoding.search(self.user, "Gdansk")
        self.assertEqual(caught.exception.status_code, 402)
        self.assertIn("compute mana", str(caught.exception))
        opened.assert_not_called()
        self.assertFalse(LocationsUsageEvent.objects.exists())

    def test_the_price_hint_names_the_price(self):
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.user
        html = Template('{% load quota_tags %}{% price_hint "locations.geocode" %}').render(
            Context({"request": request}))
        self.assertIn("0.5", html)


# ---------------------------------------------------------------------------
# The JSON doors
# ---------------------------------------------------------------------------

@override_settings(LOCATIONS_GEOCODING=ON)
class EndpointTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "T", "author": "t", "publication_year": 2026})
        cls.user = User.objects.create_user("ada", password="x")

    def setUp(self):
        cache.clear()
        unthrottled(self)
        self.client.force_login(self.user)
        self.search_url = reverse("locations:geocode_search")
        self.reverse_url = reverse("locations:geocode_reverse")

    def test_the_urls(self):
        self.assertEqual(self.search_url, "/locations/geocode/search/")
        self.assertEqual(self.reverse_url, "/locations/geocode/reverse/")
        self.assertEqual(reverse("locations:location_search_api"), "/locations/locations/search/")

    def test_search_answers_json(self):
        with answering(GDANSK):
            first = self.client.post(self.search_url, {"q": "Gdansk"})
        second = self.client.post(self.search_url, {"q": "Gdansk"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["results"][0]["lng"], 18.6466)
        self.assertEqual((first.json()["cached"], second.json()["cached"]), (False, True))

    def test_reverse_answers_json(self):
        with answering(DLUGA):
            response = self.client.post(self.reverse_url, {"lat": "54.352", "lng": "18.6466"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {"lat", "lng", "label", "fields", "found", "cached"})
        self.assertEqual(set(body["fields"]), set(geocode.ADDRESS_FIELDS))
        self.assertEqual(body["fields"]["country_name"], "PL")

    def test_the_old_search_door_is_the_charged_search(self):
        if not HAS_GIS:
            self.skipTest("the map page, and its old search door, 404 without GIS")
        with answering(GDANSK):
            response = self.client.post(reverse("locations:location_search_api"), {"q": "Gdansk"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(LocationsUsageEvent.objects.count(), 1)

    def test_get_is_refused_everywhere(self):
        urls = [self.search_url, self.reverse_url]
        if HAS_GIS:
            urls.append(reverse("locations:location_search_api"))
        with mock.patch(URLOPEN) as opened:
            for url in urls:
                with self.subTest(url):
                    self.assertEqual(self.client.get(url, {"q": "Gdansk", "lat": 1, "lng": 1})
                                     .status_code, 405)
        opened.assert_not_called()

    def test_sign_in_is_required(self):
        self.client.logout()
        with mock.patch(URLOPEN) as opened:
            for url in (self.search_url, self.reverse_url):
                response = self.client.post(url, {"q": "Gdansk", "lat": 1, "lng": 1})
                self.assertEqual(response.status_code, 302)
        opened.assert_not_called()

    def test_csrf_is_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with mock.patch(URLOPEN) as opened:
            for url in (self.search_url, self.reverse_url):
                self.assertEqual(client.post(url, {"q": "Gdansk", "lat": 1, "lng": 1}).status_code,
                                 403)
        opened.assert_not_called()
        self.assertFalse(LocationsUsageEvent.objects.exists())

        token = "a" * 32
        client.cookies["csrftoken"] = token
        with answering(GDANSK):
            response = client.post(self.search_url, {"q": "Gdansk"}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_refusals_answer_json_with_their_status(self):
        from toto.quota.charge import InsufficientFunds

        def post(url, data):
            response = self.client.post(url, data)
            return response.status_code, bool(response.json().get("error"))

        self.assertEqual(post(self.search_url, {"q": "a"}), (400, True))
        self.assertEqual(post(self.reverse_url, {"lat": "95", "lng": "0"}), (400, True))
        with failing(URLError("down")), self.assertLogs("toto.locations.geocode", "WARNING"):
            self.assertEqual(post(self.search_url, {"q": "Gdansk"}), (503, True))
        with mock.patch("toto.locations.billing.check_funds",
                        side_effect=InsufficientFunds("RED", 1, 0)):
            self.assertEqual(post(self.reverse_url, {"lat": "1", "lng": "1"}), (402, True))
        self.assertFalse(LocationsUsageEvent.objects.exists())

    @override_settings(LOCATIONS_GEOCODING=OFF)
    def test_a_host_with_geocoding_off_answers_404_json(self):
        response = self.client.post(self.search_url, {"q": "Gdansk"})
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.json())

    def test_a_throttled_lookup_says_when_to_come_back(self):
        with mock.patch.object(geocoding, "PROVIDER_LIMIT", 0), \
                mock.patch.object(geocoding, "PROVIDER_WAIT", 0):
            response = self.client.post(self.search_url, {"q": "Gdansk"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response["Retry-After"], "1")
        self.assertIn("error", response.json())


class GisOffTests(SimpleTestCase):
    def test_geocoding_survives_a_host_without_gis(self):
        """Every other locations door 404s on a GIS-off host; these two answer
        with text and floats and must not."""
        import toto.locations.urls as urls

        try:
            with override_settings(HAS_GIS=False):
                importlib.reload(urls)
                callbacks = {p.name: p.callback for p in urls.urlpatterns}
            self.assertEqual(callbacks["geocode_search"].__name__, "geocode_search")
            self.assertEqual(callbacks["geocode_reverse"].__name__, "geocode_reverse")
            self.assertEqual(callbacks["locations_all"].__name__, "_gis_disabled")
        finally:
            importlib.reload(urls)
            clear_url_caches()
