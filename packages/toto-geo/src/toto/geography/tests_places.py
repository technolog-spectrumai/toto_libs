"""Place-name search (2026-10-06): charged once per answered search, replayed
free under its op, and nothing for a refusal.

Lifted from the parked map's ``tests_geocoding`` and ``tests_more_geocoding``
with the provider patched (``urlopen``); every test clears the cache, where
the answer cache and both throttles live.

    manage.py test toto.geography.tests_places
"""

import http.client
import json
from datetime import timedelta
from decimal import Decimal
from unittest import mock
from urllib.error import HTTPError, URLError

from django.contrib.auth.models import AnonymousUser
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.geography import charging, geocode, places
from toto.geography.charging import Refusal
from toto.geography.models import GeographyQuotaPolicy, GeographyUsageEvent
from toto.geography.testing import (Economy, client_of, fresh_cache, member, no_funds, op, post,
                                    refusing_ledger)

ON = {"enabled": True}
OFF = {"enabled": False}
URLOPEN = "toto.geography.geocode.urlopen"

GDANSK = [{
    "display_name": "Gdańsk, województwo pomorskie, Polska",
    "name": "Gdańsk", "lat": "54.3520", "lon": "18.6466", "type": "city",
    "address": {"city": "Gdańsk", "state": "województwo pomorskie", "country_code": "pl"},
}]
WARSAW = [{
    "display_name": "Warszawa, województwo mazowieckie, Polska",
    "name": "Warszawa", "lat": "52.2297", "lon": "21.0122", "type": "city",
    "address": {"city": "Warszawa", "country_code": "pl"},
}]

PROVIDER_FAILURES = (
    ("http error", HTTPError("https://geo", 429, "Too Many Requests", {}, None)),
    ("url error", URLError("name or service not known")),
    ("reset", ConnectionResetError("reset by peer")),
    ("timeout", TimeoutError("timed out")),
    ("truncated", http.client.IncompleteRead(b"[{")),
)
BAD_BODIES = (("not json", b"<html>busy</html>"), ("not utf-8", b"\xff\xfe\xfa"),
              ("not a list", b'{"error": "busy"}'))


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
    """One provider call a second is the production pace; a test asking
    twice in a row should not sleep for it."""
    patcher = mock.patch.object(places, "PROVIDER_LIMIT", 1000)
    patcher.start()
    test.addCleanup(patcher.stop)


class CopiedAdapterTests(SimpleTestCase):
    """``geography/geocode.py`` is a copy of the parked map's adapter: the
    same code under this app's name, reading the same setting."""

    def test_the_copy_is_the_original_byte_for_byte(self):
        from pathlib import Path

        copy = Path(geocode.__file__)
        original = copy.parent.parent / "locations" / "geocode.py"
        if not original.exists():
            self.skipTest("the parked map's sources are not beside this app")
        self.assertEqual(copy.read_bytes(), original.read_bytes())

    def test_it_logs_under_its_own_name_and_imports_no_map(self):
        self.assertEqual(geocode.logger.name, "toto.geography.geocode")
        from pathlib import Path

        for module in ("geocode", "places", "routing", "charging", "saves", "views"):
            source = (Path(geocode.__file__).parent / f"{module}.py").read_text(encoding="utf-8")
            self.assertNotIn("toto.locations import", source, module)
            self.assertNotIn("from toto.locations", source, module)

    @override_settings(LOCATIONS_GEOCODING=ON)
    def test_results_carry_float_coordinates(self):
        with answering(GDANSK):
            results = geocode.search_places("Gdansk")
        self.assertEqual((results[0]["lat"], results[0]["lng"]), (54.352, 18.6466))

    @override_settings(LOCATIONS_GEOCODING=ON)
    def test_the_raising_search_tells_failure_from_nothing_found(self):
        with answering([]):
            self.assertEqual(geocode.search_places("zzzz"), [])
        for name, exc in PROVIDER_FAILURES:
            with self.subTest(name), failing(exc), \
                    self.assertLogs("toto.geography.geocode", "WARNING") as logs:
                with self.assertRaises(geocode.GeocodingUnavailable):
                    geocode.search_places("Gdańsk")
            self.assertNotIn("Gdańsk", "\n".join(logs.output))


@override_settings(LOCATIONS_GEOCODING=ON, GEOGRAPHY_GEOCODE_CACHE_SECONDS=3600)
class ServiceTestCase(TestCase):
    def setUp(self):
        fresh_cache(self)
        unthrottled(self)
        self.user, self.person = member("ada")

    def events(self):
        return GeographyUsageEvent.objects.filter(metric_code="geography.lookup")

    def assertRefused(self, status, call):
        with self.assertRaises(Refusal) as caught:
            call()
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception


class ChargingTests(ServiceTestCase):
    def test_a_search_answers_label_and_point_and_is_charged_once(self):
        with answering(GDANSK):
            results, charged = places.search(self.user, "Gdansk", op())
        self.assertEqual(results, [{"label": "Gdańsk, województwo pomorskie, Polska",
                                    "lat": 54.352, "lng": 18.6466}])
        self.assertTrue(charged)
        event = self.events().get()
        self.assertEqual((event.quantity, event.unit, event.user), (Decimal("1"), "lookup",
                                                                    self.user))
        self.assertEqual(event.source_label, "Place search")

    def test_the_event_key_and_its_metadata(self):
        key = op()
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
        event = self.events().get()
        self.assertEqual(event.idempotency_key, f"geography.lookup:{self.user.pk}:{key}")
        self.assertEqual(set(event.metadata), {"request"})
        self.assertRegex(event.metadata["request"], r"^[0-9a-f]{64}$")
        self.assertEqual(event.metadata["request"],
                         charging.digest(self.user, key, {"q": "gdansk"}))

    def test_the_same_op_and_query_twice_is_one_event(self):
        key = op()
        with answering(GDANSK) as opened:
            first = places.search(self.user, "Gdansk", key)
            second = places.search(self.user, "  gdansk ", key)
        self.assertEqual((first[1], second[1]), (True, False))
        self.assertEqual(first[0], second[0])
        self.assertEqual(self.events().count(), 1)
        self.assertEqual(opened.call_count, 1, "the replay was answered from the cache")

    def test_the_same_op_with_another_query_is_409_and_asks_nobody(self):
        key = op()
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
        with mock.patch(URLOPEN) as opened:
            self.assertRefused(409, lambda: places.search(self.user, "Warsaw", key))
        opened.assert_not_called()
        self.assertEqual(self.events().count(), 1)

    def test_a_replay_after_ten_minutes_is_409(self):
        key = op()
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
        self.events().update(occurred_at=timezone.now()
                             - timedelta(seconds=charging.REPLAY_SECONDS + 5))
        with mock.patch(URLOPEN) as opened:
            self.assertRefused(409, lambda: places.search(self.user, "Gdansk", key))
        opened.assert_not_called()
        self.assertEqual(self.events().count(), 1)

    def test_another_member_s_op_is_their_own(self):
        key = op()
        other, _person = member("bob")
        with answering(GDANSK):
            self.assertTrue(places.search(self.user, "Gdansk", key)[1])
            self.assertTrue(places.search(other, "Gdansk", key)[1])
        self.assertEqual(self.events().count(), 2)

    def test_a_failed_call_then_the_same_op_succeeding_is_one_charge(self):
        key = op()
        with failing(URLError("down")), self.assertLogs("toto.geography.geocode", "WARNING"):
            self.assertRefused(503, lambda: places.search(self.user, "Gdansk", key))
        self.assertFalse(self.events().exists())
        with answering(GDANSK):
            results, charged = places.search(self.user, "Gdansk", key)
        self.assertTrue(charged)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.events().count(), 1)

    def test_an_empty_answer_is_given_and_not_charged(self):
        with answering([]):
            results, charged = places.search(self.user, "zzzzzz", op())
        self.assertEqual((results, charged), ([], False))
        self.assertFalse(self.events().exists())

    def test_a_new_search_answered_from_the_cache_is_still_charged(self):
        with answering(GDANSK) as opened:
            places.search(self.user, "Gdansk", op())
            results, charged = places.search(self.user, "Gdansk", op())
        self.assertTrue(charged)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.events().count(), 2)

    def test_two_at_once_under_one_op_settle_once(self):
        """The loser of a race meets the unique key at its own insert: its
        savepoint rolls back alone and it answers as a replay."""
        key = op()
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
            with mock.patch("toto.geography.places.charging.known", return_value=False), \
                    mock.patch("toto.geography.charging.charge") as charged_again:
                results, charged = places.search(self.user, "Gdansk", key)
        self.assertEqual(len(results), 1)
        self.assertFalse(charged)
        charged_again.assert_not_called()
        self.assertEqual(self.events().count(), 1)
        # The transaction is still usable after the caught duplicate.
        self.assertTrue(GeographyUsageEvent.objects.exists())

    def test_two_at_once_under_one_op_with_another_query_is_409(self):
        """Two searches sent at once under one op, each for another place:
        the second passed its first check before the first one's event was
        there (``known`` is patched to answer as that race does) and meets
        the key at its own insert. It is no replay: 409, nothing served,
        nothing charged."""
        key = op()
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
        with answering(WARSAW), \
                mock.patch("toto.geography.places.charging.known", return_value=False), \
                mock.patch("toto.geography.charging.charge") as charged_again:
            refusal = self.assertRefused(409, lambda: places.search(self.user, "Warsaw", key))
        self.assertIn("already used", str(refusal))
        charged_again.assert_not_called()
        self.assertEqual(self.events().count(), 1)
        self.assertEqual(self.events().get().metadata["request"],
                         charging.digest(self.user, key, {"q": "gdansk"}))
        # The transaction is still usable after the refused duplicate.
        self.assertTrue(GeographyUsageEvent.objects.exists())

    def test_a_key_taken_longer_ago_than_the_replay_window_is_409(self):
        """The same request, but its event is older than the ten minutes:
        the key bought that request once, not for ever."""
        key = op()
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
            self.events().update(occurred_at=timezone.now()
                                 - timedelta(seconds=charging.REPLAY_SECONDS + 5))
            with mock.patch("toto.geography.places.charging.known", return_value=False):
                self.assertRefused(409, lambda: places.search(self.user, "Gdansk", key))
        self.assertEqual(self.events().count(), 1)

    def test_provider_failures_are_503_and_free(self):
        for name, exc in PROVIDER_FAILURES:
            cache.clear()
            with self.subTest(name), failing(exc), \
                    self.assertLogs("toto.geography.geocode", "WARNING"):
                refusal = self.assertRefused(503, lambda: places.search(self.user, "Gdansk", op()))
                self.assertIn("not charged", str(refusal))
        for name, body in BAD_BODIES:
            cache.clear()
            with self.subTest(name), answering(body), \
                    self.assertLogs("toto.geography.geocode", "WARNING"):
                self.assertRefused(503, lambda: places.search(self.user, "Gdansk", op()))
        self.assertFalse(self.events().exists())


class RefusalTests(ServiceTestCase):
    def test_bad_input_is_400_before_anything(self):
        with mock.patch(URLOPEN) as opened:
            for query in ("", " ", "x", "x" * 201, None, 7, ["Gdansk"]):
                with self.subTest(query=query):
                    self.assertRefused(400, lambda: places.search(self.user, query, op()))
            # A lone surrogate (JSON's "\ud800") cannot be written as UTF-8:
            # the cache key and the provider's address would both fail on it.
            for query in ("ab\ud800", "\udfffGdansk", "Gda\x00nsk"):
                with self.subTest(query=ascii(query)):
                    self.assertRefused(400, lambda: places.search(self.user, query, op()))
            for key in (None, "", "not-a-uuid", 7, "123"):
                with self.subTest(op=key):
                    self.assertRefused(400, lambda: places.search(self.user, "Gdansk", key))
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    @override_settings(LOCATIONS_GEOCODING=OFF)
    def test_a_host_with_search_off_answers_404(self):
        with mock.patch(URLOPEN) as opened:
            self.assertRefused(404, lambda: places.search(self.user, "Gdansk", op()))
        opened.assert_not_called()

    def test_nobody_anonymous_searches(self):
        with mock.patch(URLOPEN) as opened:
            for user in (None, AnonymousUser()):
                self.assertRefused(403, lambda: places.search(user, "Gdansk", op()))
        opened.assert_not_called()

    def test_the_per_member_limit_is_429_and_uncharged(self):
        with mock.patch.object(places, "USER_LIMIT", 1), answering(GDANSK):
            places.search(self.user, "Gdansk", op())
            refusal = self.assertRefused(429, lambda: places.search(self.user, "Gdansk", op()))
        self.assertGreaterEqual(refusal.retry_after, 1)
        self.assertEqual(self.events().count(), 1)

    def test_a_replay_is_bounded_by_the_per_member_limit(self):
        key = op()
        with mock.patch.object(places, "USER_LIMIT", 2), answering(GDANSK):
            places.search(self.user, "Gdansk", key)
            places.search(self.user, "Gdansk", key)
            self.assertRefused(429, lambda: places.search(self.user, "Gdansk", key))

    def test_the_provider_throttle_is_429_and_uncharged(self):
        with mock.patch.object(places, "PROVIDER_LIMIT", 0), \
                mock.patch.object(places, "PROVIDER_WAIT", 0), mock.patch(URLOPEN) as opened:
            self.assertRefused(429, lambda: places.search(self.user, "Gdansk", op()))
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_funds_are_asked_before_the_provider(self):
        from toto.quota.charge import InsufficientFunds

        with no_funds(), mock.patch(URLOPEN) as opened:
            with self.assertRaises(InsufficientFunds) as caught:
                places.search(self.user, "Gdansk", op())
        self.assertEqual(caught.exception.status_code, 402)
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_a_ledger_refusal_after_the_answer_leaves_no_event(self):
        from toto.quota.charge import InsufficientFunds

        with refusing_ledger(), answering(GDANSK):
            with self.assertRaises(InsufficientFunds):
                places.search(self.user, "Gdansk", op())
        self.assertFalse(self.events().exists())

    def test_the_daily_cap_refuses_the_search_after_the_last(self):
        from toto.quota.api import QuotaExceeded

        GeographyQuotaPolicy.objects.create(metric_code="geography.lookup", limit=1,
                                            unit="lookup")
        with answering(GDANSK):
            places.search(self.user, "Gdansk", op())
        with mock.patch(URLOPEN) as opened, self.assertRaises(QuotaExceeded):
            places.search(self.user, "Warsaw", op())
        opened.assert_not_called()


class AnswerCacheTests(ServiceTestCase):
    """One hour, keyed-hash keys, tied to no member."""

    def keys(self):
        return {key: value for key, value in getattr(cache, "_cache", {}).items()}

    def test_no_key_or_value_holds_a_member_or_the_query(self):
        if not hasattr(cache, "_cache"):
            self.skipTest("the test cache cannot be listed")
        with answering(WARSAW):
            places.search(self.user, "Warsaw old town", op())
        stored = self.keys()
        answer_keys = [key for key in stored if "geography:places:" in key and ":rl:" not in key
                       and "rl:geography" not in key]
        self.assertEqual(len(answer_keys), 1)
        import pickle

        for key in stored:
            self.assertNotIn("warsaw", key.casefold())
            self.assertNotIn("old town", key.casefold())
        value = pickle.loads(stored[answer_keys[0]])
        text = repr(value)
        self.assertNotIn("old town", text.casefold())
        self.assertNotIn(self.user.username, text)
        self.assertNotIn(f"user", text.casefold().replace("user_agent", ""))
        self.assertRegex(answer_keys[0].split("geography:places:")[1], r"^[0-9a-f]{64}$")

    def test_the_key_is_keyed_not_a_plain_hash(self):
        import hashlib

        from toto.geography.geocode import geocoding_settings

        key = places._cache_key(geocoding_settings(), "gdansk")
        self.assertNotIn(hashlib.sha256(b"gdansk").hexdigest(), key)
        with override_settings(SECRET_KEY="another-secret"):
            self.assertNotEqual(places._cache_key(geocoding_settings(), "gdansk"), key)

    def test_it_is_kept_one_hour(self):
        self.assertEqual(places.cache_seconds(), 3600)
        with mock.patch("toto.geography.places.cache.set") as stored, answering(GDANSK):
            places.search(self.user, "Gdansk", op())
        self.assertEqual(stored.call_args.args[2], 3600)

    @override_settings(GEOGRAPHY_GEOCODE_CACHE_SECONDS=0)
    def test_zero_turns_it_off(self):
        with answering(GDANSK) as opened:
            places.search(self.user, "Gdansk", op())
            places.search(self.user, "Gdansk", op())
        self.assertEqual(opened.call_count, 2)

    def test_a_cache_outage_is_a_miss_not_a_refusal(self):
        with mock.patch("toto.geography.places.cache.get", side_effect=RuntimeError("down")), \
                mock.patch("toto.geography.places.cache.set", side_effect=RuntimeError("down")), \
                answering(GDANSK), self.assertLogs("toto.geography.places", "WARNING"):
            results, charged = places.search(self.user, "Gdansk", op())
        self.assertEqual(len(results), 1)
        self.assertTrue(charged)


@override_settings(LOCATIONS_GEOCODING=ON)
class LedgerTests(TestCase):
    """With the host's real rate card: one ledger charge per answered search."""

    def setUp(self):
        fresh_cache(self)
        unthrottled(self)
        self.economy = Economy(self)
        self.user, self.person = member("ada")

    def test_a_search_costs_half_a_compute_mana_once(self):
        key = op()
        before = self.economy.held(self.user, "compute")
        with answering(GDANSK):
            places.search(self.user, "Gdansk", key)
            places.search(self.user, "Gdansk", key)
        self.assertEqual(before - self.economy.held(self.user, "compute"), Decimal("0.5"))
        self.assertEqual(self.economy.charges("geography.lookup"), 1)

    def test_an_empty_pool_is_402_before_the_provider(self):
        from toto.quota.charge import InsufficientFunds

        self.economy.empty(self.user, "compute")
        with mock.patch(URLOPEN) as opened, self.assertRaises(InsufficientFunds) as caught:
            places.search(self.user, "Gdansk", op())
        self.assertEqual(caught.exception.status_code, 402)
        opened.assert_not_called()
        self.assertFalse(GeographyUsageEvent.objects.exists())
        self.assertEqual(self.economy.charges(), 0)

    def test_the_ledger_never_learns_what_was_searched(self):
        from toto.tariffs.models import UsageRecord

        with answering(GDANSK):
            places.search(self.user, "Gdansk Dluga", op())
        for row in UsageRecord.objects.all():
            text = repr({field.name: getattr(row, field.attname)
                         for field in row._meta.concrete_fields})
            for secret in ("Gdansk", "Dluga", "Gdańsk", "54.35", "18.64"):
                self.assertNotIn(secret, text)

    def test_the_price_hint_names_the_price(self):
        from django.template import Context, Template
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = self.user
        html = Template('{% load quota_tags %}{% price_hint "geography.lookup" %}').render(
            Context({"request": request}))
        self.assertIn("0.5", html)


@override_settings(LOCATIONS_GEOCODING=ON)
class DoorTests(TestCase):
    """``POST /geography/api/search/`` ``{q, op}``."""

    def setUp(self):
        fresh_cache(self)
        unthrottled(self)
        self.user, self.person = member("ada")
        self.client = client_of(self.user)
        self.url = reverse("geography:search")

    def test_it_answers_results_and_charged_and_is_never_stored(self):
        with answering(GDANSK):
            response = post(self.client, self.url, {"q": "Gdansk", "op": op()})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(set(data), {"results", "charged"})
        self.assertTrue(data["charged"])
        self.assertEqual(set(data["results"][0]), {"label", "lat", "lng"})

    def test_get_is_405(self):
        response = self.client.get(self.url, {"q": "Gdansk"})
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "POST")
        self.assertFalse(GeographyUsageEvent.objects.exists())

    def test_a_lost_race_with_another_query_is_409_and_holds_no_result(self):
        key = op()
        with answering(GDANSK):
            self.assertEqual(post(self.client, self.url, {"q": "Gdansk", "op": key}).status_code,
                             200)
        with answering(WARSAW), \
                mock.patch("toto.geography.places.charging.known", return_value=False):
            response = post(self.client, self.url, {"q": "Warsaw", "op": key})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(set(response.json()), {"error"})
        self.assertNotIn("Warszawa", response.content.decode())
        self.assertEqual(GeographyUsageEvent.objects.count(), 1)

    def test_no_op_is_400_and_a_used_one_409(self):
        with answering(GDANSK):
            self.assertEqual(post(self.client, self.url, {"q": "Gdansk"}).status_code, 400)
            key = op()
            self.assertEqual(post(self.client, self.url, {"q": "Gdansk", "op": key}).status_code,
                             200)
            response = post(self.client, self.url, {"q": "Warsaw", "op": key})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn("results", response.json())
        self.assertIn("error", response.json())

    def test_a_body_that_is_no_object_is_400(self):
        for body in ("[]", "nonsense", '"Gdansk"'):
            response = self.client.post(self.url, data=body, content_type="application/json")
            self.assertEqual(response.status_code, 400, body)

    def test_a_query_that_is_no_text_a_database_takes_is_400_as_json(self):
        with mock.patch(URLOPEN) as opened:
            for query in ("ab\ud800", "Gda\x00nsk"):
                with self.subTest(query=ascii(query)):
                    response = post(self.client, self.url, {"q": query, "op": op()})
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response["Cache-Control"], "no-store")
                    self.assertEqual(set(response.json()), {"error"})
        opened.assert_not_called()
        self.assertFalse(GeographyUsageEvent.objects.exists())

    def test_a_ledger_refusal_after_the_answer_is_402_with_no_results(self):
        with refusing_ledger(), answering(GDANSK):
            response = post(self.client, self.url, {"q": "Gdansk", "op": op()})
        self.assertEqual(response.status_code, 402)
        self.assertNotIn("results", response.json())
        self.assertNotIn("Gdańsk", response.content.decode())
        self.assertFalse(GeographyUsageEvent.objects.exists())

    def test_no_funds_is_402_and_a_dead_provider_503(self):
        with no_funds(), mock.patch(URLOPEN) as opened:
            self.assertEqual(post(self.client, self.url, {"q": "Gdansk", "op": op()}).status_code,
                             402)
        opened.assert_not_called()
        with failing(URLError("down")), self.assertLogs("toto.geography.geocode", "WARNING"):
            self.assertEqual(post(self.client, self.url, {"q": "Gdansk", "op": op()}).status_code,
                             503)

    def test_the_throttle_is_429_with_retry_after(self):
        with mock.patch.object(places, "USER_LIMIT", 0):
            response = post(self.client, self.url, {"q": "Gdansk", "op": op()})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)

    def test_it_wants_a_csrf_token(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.user)
        response = post(strict, self.url, {"q": "Gdansk", "op": op()})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(GeographyUsageEvent.objects.exists())
