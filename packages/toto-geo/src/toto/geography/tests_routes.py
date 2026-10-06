"""Route search (2026-10-06): calculated, answered, charged once and
forgotten. The line reaches no table, no cache and no audit record.

    manage.py test toto.geography.tests_routes
"""

import io
import json
import re
from unittest import mock
from urllib.error import HTTPError, URLError

from django.apps import apps
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from toto.geography import places, provider, routing
from toto.geography.models import GeographyUsageEvent
from toto.geography.testing import (Economy, ProviderAnswer, client_of, fresh_cache, member,
                                    no_funds, op, post, provider_answering, refusing_ledger)

#: Where the router is asked: the capped reader's ``urlopen``.
URLOPEN = "toto.geography.provider.urlopen"
A = {"lat": 52.2297, "lng": 21.0122}
B = {"lat": 54.352, "lng": 18.6466}
LINE = [[21.0122, 52.2297], [20.1234, 53.4321], [18.6466, 54.352]]
OSRM = {"code": "Ok", "routes": [{"distance": 339512.4, "duration": 12345.6,
                                  "geometry": {"type": "LineString", "coordinates": LINE}}]}
ROUTING = {"enabled": True, "timeout": 10, "endpoints": {
    "car": "https://router.test/car", "bicycle": "https://router.test/bike",
    "foot": "https://router.test/foot"}}

#: A number with four or more decimals: what a coordinate looks like.
FLOAT = re.compile(r"-?\d{1,3}\.\d{4,}")


def answering(payload=OSRM, piece=None):
    return provider_answering(URLOPEN, payload, piece)


def route_of(**changes):
    """The router's answer with one thing of its first route changed; a
    value of ``...`` takes the key out."""
    route = {**OSRM["routes"][0], **changes}
    return {"code": "Ok", "routes": [{key: value for key, value in route.items()
                                      if value is not ...}]}


def line_of(coordinates):
    """The router's answer with other corners; ``...`` gives a line with no
    ``coordinates`` at all."""
    geometry = {"type": "LineString"}
    if coordinates is not ...:
        geometry["coordinates"] = coordinates
    return route_of(geometry=geometry)


def body(**changes):
    return {"from": A, "to": B, "mode": "car", "op": op(), **changes}


@override_settings(GEOGRAPHY_ROUTING=ROUTING)
class RouteTestCase(TestCase):
    #: With the host's real ledger (the member is made after it, so their
    #: pools are filled); the test is skipped on a host that bills nothing.
    billed = False

    def setUp(self):
        fresh_cache(self)
        self.economy = Economy(self) if self.billed else None
        patcher = mock.patch.object(places, "PROVIDER_LIMIT", 1000)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.user, self.person = member("ada")
        self.client = client_of(self.user)
        self.url = reverse("geography:route")

    def events(self):
        return GeographyUsageEvent.objects.filter(metric_code="geography.route")


class DoorTests(RouteTestCase):
    def test_get_is_405_with_the_coordinates_in_no_address(self):
        response = self.client.get(self.url, {"from": "52.2,21.0", "to": "54.3,18.6"})
        self.assertEqual(response.status_code, 405)
        self.assertFalse(self.events().exists())

    def test_a_route_between_two_coordinates_is_charged_once(self):
        with answering() as opened:
            response = post(self.client, self.url, body())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        self.assertEqual(set(data), {"line", "distance_km", "duration_min", "charged"})
        self.assertEqual(data["line"], {"type": "LineString", "coordinates": LINE})
        self.assertEqual((data["distance_km"], data["duration_min"], data["charged"]),
                         (339.51, 205.8, True))
        self.assertEqual(self.events().count(), 1)
        request = opened.call_args.args[0]
        self.assertTrue(request.full_url.startswith(
            "https://router.test/car/21.0122,52.2297;18.6466,54.352?"))
        self.assertEqual(opened.call_args.kwargs["timeout"], 10)
        self.assertIn("User-agent", request.headers)

    def test_each_mode_asks_its_own_endpoint_and_there_is_no_transit(self):
        for mode, part in (("car", "/car/"), ("bicycle", "/bike/"), ("foot", "/foot/")):
            with self.subTest(mode=mode), answering() as opened:
                self.assertEqual(post(self.client, self.url, body(mode=mode)).status_code, 200)
                self.assertIn(part, opened.call_args.args[0].full_url)
        with mock.patch(URLOPEN) as opened:
            for mode in ("public_transport", "plane", "", None, 7):
                self.assertEqual(post(self.client, self.url, body(mode=mode)).status_code, 400)
        opened.assert_not_called()
        self.assertEqual([key for key, _label in routing.modes()], ["car", "bicycle", "foot"])

    def test_an_end_that_is_no_bare_coordinate_pair_is_400_and_free(self):
        bad_ends = (
            {"address": 7}, {"q": "Warsaw"}, {"lat": 52.2, "lng": 21.0, "address": 7},
            {"lat": 52.2, "lng": 21.0, "q": "home"}, {"id": 3}, {"person": "ada"},
            "Warsaw", 7, None, [52.2, 21.0], {"lat": "52.2", "lng": "21.0"},
            {"lat": 91, "lng": 0}, {"lat": 52.2},
            {"lat": 10 ** 400, "lng": 0}, {"lat": 0, "lng": -(10 ** 400)},
        )
        with mock.patch(URLOPEN) as opened:
            for end in bad_ends:
                for side in ("from", "to"):
                    with self.subTest(end=repr(end)[:60], side=side):
                        response = post(self.client, self.url, body(**{side: end}))
                        self.assertEqual(response.status_code, 400)
                        self.assertEqual(response["Cache-Control"], "no-store")
                        self.assertEqual(set(response.json()), {"error"})
            self.assertEqual(post(self.client, self.url, body(to=A)).status_code, 400)
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_no_op_is_400_and_the_same_op_with_other_ends_409(self):
        with answering() as opened:
            payload = body()
            del payload["op"]
            self.assertEqual(post(self.client, self.url, payload).status_code, 400)
            opened.assert_not_called()
            key = op()
            self.assertEqual(post(self.client, self.url, body(op=key)).status_code, 200)
            response = post(self.client, self.url,
                            body(op=key, to={"lat": 50.06, "lng": 19.94}))
            self.assertEqual(response.status_code, 409)
            self.assertNotIn("line", response.json())
            self.assertEqual(post(self.client, self.url, body(op=key, mode="foot")).status_code,
                             409)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(self.events().count(), 1)

    def test_two_at_once_under_one_op_with_other_ends_is_409_and_no_line(self):
        """Two routes sent at once under one op, to two places: the second
        passed its first check before the first one's event was there
        (``known`` is patched to answer as that race does), was calculated,
        and meets the key at its own insert. 409, no line, one charge."""
        key = op()
        with answering():
            self.assertEqual(post(self.client, self.url, body(op=key)).status_code, 200)
        with answering(), mock.patch("toto.geography.routing.charging.known",
                                     return_value=False), \
                mock.patch("toto.geography.charging.charge") as charged_again:
            response = post(self.client, self.url,
                            body(op=key, to={"lat": 50.06, "lng": 19.94}))
            other_mode = post(self.client, self.url, body(op=key, mode="foot"))
        self.assertEqual((response.status_code, other_mode.status_code), (409, 409))
        self.assertEqual(set(response.json()), {"error"})
        self.assertIsNone(FLOAT.search(response.content.decode()))
        charged_again.assert_not_called()
        self.assertEqual(self.events().count(), 1)

    def test_two_at_once_under_one_op_for_the_same_route_is_a_replay(self):
        key = op()
        with answering():
            post(self.client, self.url, body(op=key))
            with mock.patch("toto.geography.routing.charging.known", return_value=False), \
                    mock.patch("toto.geography.charging.charge") as charged_again:
                response = post(self.client, self.url, body(op=key))
        self.assertEqual(response.status_code, 200)
        self.assertEqual((response.json()["charged"], response.json()["line"]["coordinates"]),
                         (False, LINE))
        charged_again.assert_not_called()
        self.assertEqual(self.events().count(), 1)

    def test_a_replay_calculates_again_and_charges_nothing_more(self):
        key = op()
        with answering() as opened:
            first = post(self.client, self.url, body(op=key)).json()
            second = post(self.client, self.url, body(op=key)).json()
        self.assertEqual((first["charged"], second["charged"]), (True, False))
        self.assertEqual(second["line"], first["line"])
        self.assertEqual(opened.call_count, 2, "a route is never cached")
        self.assertEqual(self.events().count(), 1)

    def test_the_router_fails_no_event_and_503(self):
        failures = (URLError("down"), TimeoutError("slow"), ConnectionResetError("reset"),
                    HTTPError("https://router.test", 500, "Server Error", {}, None))
        for exc in failures:
            with self.subTest(exc=type(exc).__name__), mock.patch(URLOPEN, side_effect=exc), \
                    self.assertLogs("toto.geography.routing", "WARNING") as logs:
                response = post(self.client, self.url, body())
                self.assertEqual(response.status_code, 503)
                self.assertIn("not charged", response.json()["error"])
            self.assertIsNone(FLOAT.search("\n".join(logs.output)))
        for payload in ({"code": "Ok", "routes": []}, {"code": "InvalidUrl"}, ["x"],
                        {"code": "Ok", "routes": [{"geometry": "abc"}]}):
            with self.subTest(payload=payload), answering(payload), \
                    self.assertLogs("toto.geography.routing", "WARNING"):
                self.assertEqual(post(self.client, self.url, body()).status_code, 503)
        self.assertFalse(self.events().exists())

    def test_an_answer_that_is_not_shaped_as_a_route_is_503_and_free(self):
        """Each of these is HTTP 200 with ``"code": "Ok"``. They used to be a
        500 (a KeyError, an AttributeError, a TypeError), or a charged
        answer the page could not read or draw."""
        odd = (
            {"code": "Ok", "routes": {"a": 1}}, {"code": "Ok", "routes": ["x"]},
            {"code": "Ok", "routes": [5]}, {"code": "Ok", "routes": 5},
            {"code": "Ok", "routes": "abc"}, {"code": "Ok", "routes": [[OSRM["routes"][0]]]},
            route_of(distance=None), route_of(duration=None), route_of(distance=...),
            route_of(duration=...), route_of(distance="339512.4"), route_of(duration=True),
            route_of(distance=-1), route_of(distance=10 ** 400), route_of(duration=[1]),
            route_of(geometry=None), route_of(geometry=...),
            route_of(geometry={"type": "Polygon", "coordinates": LINE}),
            line_of("abc"), line_of([]), line_of(...), line_of(None), line_of(LINE[:1]),
            line_of({"0": [1, 2], "1": [3, 4]}), line_of([[21.0, "52.2"], [18.6, 54.3]]),
            line_of([[21.0], [18.6, 54.3]]), line_of([[True, False], [18.6, 54.3]]),
            line_of([21.0, 52.2, 18.6, 54.3]), line_of([[21.0, None], [18.6, 54.3]]),
            b'{"code": "Ok", "routes": [{"distance": 1e999, "duration": 5, "geometry": '
            b'{"type": "LineString", "coordinates": [[21, 52], [18, 54]]}}]}',
            b'{"code": "Ok", "routes": [{"distance": 5, "duration": NaN, "geometry": '
            b'{"type": "LineString", "coordinates": [[21, 52], [18, 54]]}}]}',
            b'{"code": "Ok", "routes": [{"distance": 5, "duration": 5, "geometry": '
            b'{"type": "LineString", "coordinates": [[21, 52], [Infinity, 54]]}}]}',
            b'{"code": "Ok", "routes": ' + b"[" * 100000,
        )
        for index, payload in enumerate(odd):
            with self.subTest(case=index, payload=repr(payload)[:70]), answering(payload), \
                    mock.patch.object(routing, "USER_LIMIT", 1000), \
                    self.assertLogs("toto.geography.routing", "WARNING") as logs:
                response = post(self.client, self.url, body())
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response["Cache-Control"], "no-store")
                self.assertIn("not charged", response.json()["error"])
            self.assertIsNone(FLOAT.search("\n".join(logs.output)))
        self.assertFalse(self.events().exists())

    def test_what_is_answered_is_what_the_page_can_draw(self):
        """A third number in a point (a height) is left out; whole numbers
        are numbers too."""
        with answering(line_of([[21, 52, 110.5], [18.6466, 54.352, 3]])):
            data = post(self.client, self.url, body()).json()
        self.assertEqual(data["line"], {"type": "LineString",
                                        "coordinates": [[21, 52], [18.6466, 54.352]]})
        self.assertTrue(data["charged"])
        with answering(route_of(distance=0, duration=0)):
            data = post(self.client, self.url, body()).json()
        self.assertEqual((data["distance_km"], data["duration_min"]), (0.0, 0.0))

    def test_an_answer_past_the_cap_is_503_and_free(self):
        self.assertEqual(routing.ANSWER_MAX, 8 * 1024 * 1024)
        size = len(json.dumps(OSRM).encode())
        with mock.patch.object(routing, "ANSWER_MAX", size - 1), answering(), \
                self.assertLogs("toto.geography.routing", "WARNING"):
            response = post(self.client, self.url, body())
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.events().exists())
        with mock.patch.object(routing, "ANSWER_MAX", size), answering():
            self.assertEqual(post(self.client, self.url, body()).status_code, 200)

    def test_an_answer_that_trickles_past_the_deadline_is_503_and_free(self):
        """The router sends a byte at a time and three seconds pass at
        every look at the clock: ten seconds after the request went out the
        reading stops, however much is still to come."""
        clock = iter(range(0, 100_000, 3))
        answer = []

        def opened(*args, **kwargs):
            answer.append(ProviderAnswer(json.dumps(OSRM).encode(), piece=1))
            return answer[-1]

        with mock.patch("toto.geography.provider.time.monotonic", side_effect=lambda: next(clock)), \
                mock.patch(URLOPEN, side_effect=opened), \
                self.assertLogs("toto.geography.routing", "WARNING") as logs:
            response = post(self.client, self.url, body())
        self.assertEqual(response.status_code, 503)
        self.assertIn("TimeoutError", "\n".join(logs.output))
        self.assertLessEqual(answer[0].reads, 4, "it stopped reading at the deadline")
        self.assertFalse(self.events().exists())

    def test_a_refusal_s_body_is_read_with_a_cap_too(self):
        huge = HTTPError("https://router.test", 400, "Bad Request", {},
                         io.BytesIO(b'{"code": "NoRoute", "message": "' + b"x" * 70_000 + b'"}'))
        with mock.patch(URLOPEN, side_effect=huge), \
                self.assertLogs("toto.geography.routing", "WARNING"):
            self.assertEqual(post(self.client, self.url, body()).status_code, 503)
        self.assertEqual(routing.REFUSAL_MAX, 64 * 1024)

    def test_no_route_is_answered_and_free(self):
        refused = HTTPError("https://router.test", 400, "Bad Request", {},
                            io.BytesIO(b'{"code": "NoRoute", "message": "Impossible route"}'))
        for patcher in (answering({"code": "NoRoute"}), mock.patch(URLOPEN, side_effect=refused)):
            with patcher:
                response = post(self.client, self.url, body())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"line": None, "distance_km": None,
                                               "duration_min": None, "charged": False})
        self.assertFalse(self.events().exists())

    def test_402_before_the_router_and_402_after_it_with_no_line(self):
        with no_funds(), mock.patch(URLOPEN) as opened:
            self.assertEqual(post(self.client, self.url, body()).status_code, 402)
        opened.assert_not_called()
        with refusing_ledger(), answering():
            response = post(self.client, self.url, body())
        self.assertEqual(response.status_code, 402)
        self.assertNotIn("line", response.json())
        self.assertIsNone(FLOAT.search(response.content.decode()))
        self.assertFalse(self.events().exists())

    def test_ten_a_minute_per_member(self):
        self.assertEqual((routing.USER_LIMIT, routing.USER_WINDOW), (10, 60))
        with mock.patch.object(routing, "USER_LIMIT", 1), answering():
            self.assertEqual(post(self.client, self.url, body()).status_code, 200)
            response = post(self.client, self.url, body())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(self.events().count(), 1)

    def test_the_shared_provider_throttle_is_429_and_free(self):
        with mock.patch.object(places, "PROVIDER_LIMIT", 0), \
                mock.patch.object(places, "PROVIDER_WAIT", 0), mock.patch(URLOPEN) as opened:
            self.assertEqual(post(self.client, self.url, body()).status_code, 429)
        opened.assert_not_called()
        self.assertFalse(self.events().exists())

    def test_with_the_cache_down_the_router_is_not_asked_and_nothing_is_charged(self):
        """Redis away: the limiter lets everything through uncounted. A
        fresh route, and the replay of one already paid for (which skips the
        afford check and is charged nothing), both answer 503 instead."""
        key = op()
        with answering():
            self.assertEqual(post(self.client, self.url, body(op=key)).status_code, 200)
        with mock.patch("toto.core.ratelimit.cache.incr", return_value=None), \
                mock.patch(URLOPEN) as opened, self.assertLogs("toto.core.ratelimit", "WARNING"):
            fresh = post(self.client, self.url, body())
            replays = [post(self.client, self.url, body(op=key)) for _again in range(3)]
        for response in (fresh, *replays):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertIn("nothing was charged", response.json()["error"])
            self.assertIn("Retry-After", response)
        opened.assert_not_called()
        self.assertEqual(self.events().count(), 1)

    @override_settings(GEOGRAPHY_ROUTING={"enabled": False})
    def test_a_host_with_routing_off_answers_404(self):
        with mock.patch(URLOPEN) as opened:
            self.assertEqual(post(self.client, self.url, body()).status_code, 404)
        opened.assert_not_called()

    def test_the_default_endpoints_are_the_three_public_ones(self):
        endpoints = routing.DEFAULT_ROUTING_SETTINGS["endpoints"]
        self.assertEqual(sorted(endpoints), ["bicycle", "car", "foot"])
        self.assertEqual(routing.DEFAULT_ROUTING_SETTINGS["timeout"], 10)
        for url in endpoints.values():
            self.assertTrue(url.startswith("https://routing.openstreetmap.de/"))


class ReaderTests(SimpleTestCase):
    """``provider.read_capped``: so much, and by then."""

    def test_it_reads_the_whole_answer_one_socket_read_at_a_time(self):
        answer = ProviderAnswer(b"abcdefghij", piece=3)
        self.assertEqual(provider.read_capped(answer, 10, float("inf")), b"abcdefghij")
        self.assertEqual(answer.reads, 5, "four pieces and the end")

    def test_one_byte_past_the_cap_is_a_value_error(self):
        with self.assertRaisesRegex(ValueError, "larger than this server reads"):
            provider.read_capped(ProviderAnswer(b"abcdefghijk"), 10, float("inf"))
        answer = ProviderAnswer(b"x" * 1000, piece=10)
        with self.assertRaises(ValueError):
            provider.read_capped(answer, 25, float("inf"))
        self.assertEqual(answer.reads, 3, "it stops at the read that passes the cap")

    def test_past_the_deadline_is_a_timeout_and_nothing_more_is_read(self):
        answer = ProviderAnswer(b"x" * 1000, piece=1)
        clock = iter([1.0, 2.0, 3.0, 4.0, 5.0])
        with mock.patch("toto.geography.provider.time.monotonic", side_effect=lambda: next(clock)), \
                self.assertRaises(TimeoutError):
            provider.read_capped(answer, 10_000, 3.5)
        self.assertEqual(answer.reads, 3)
        self.assertTrue(issubclass(TimeoutError, OSError), "a provider error, so a 503")

    def test_a_response_with_no_read1_is_read_all_the_same(self):
        class Plain:
            def __init__(self):
                self.left = [b"ab", b"cd", b""]

            def read(self, size):
                return self.left.pop(0)

        self.assertEqual(provider.read_capped(Plain(), 10, float("inf")), b"abcd")

    def test_a_body_that_is_no_json_or_nested_too_deep_is_a_value_error(self):
        for body in (b"<html>", b"\xff\xfe", b"[" * 100000, b'{"a":' * 50000):
            with self.subTest(body=body[:6]), self.assertRaises(ValueError):
                provider.parse(body)
        self.assertEqual(provider.parse(b'{"a": [1, 2]}'), {"a": [1, 2]})

    def test_the_deadline_runs_from_before_the_request_is_sent(self):
        clock = iter([100.0, 100.0, 100.0])
        with mock.patch("toto.geography.provider.time.monotonic", side_effect=lambda: next(clock)), \
                mock.patch("toto.geography.provider.read_capped", return_value=b"{}") as read, \
                mock.patch(URLOPEN, side_effect=lambda *a, **k: ProviderAnswer(b"{}")) as opened:
            self.assertEqual(provider.fetch_json("request", timeout=7, limit=99), {})
        self.assertEqual(opened.call_args.kwargs, {"timeout": 7})
        self.assertEqual(read.call_args.args[1:], (99, 107.0))


class ForgottenTests(RouteTestCase):
    """What a route leaves behind: counters and charges, never the line."""

    def test_the_only_new_cache_keys_are_rate_counters(self):
        if not hasattr(cache, "_cache"):
            self.skipTest("the test cache cannot be listed")
        import pickle

        self.client.get(self.url)       # the session's own keys, before the count
        before = set(cache._cache)
        with answering():
            self.assertEqual(post(self.client, self.url, body()).status_code, 200)
        new = set(cache._cache) - before
        self.assertTrue(new)
        for key in new:
            self.assertRegex(key, r"(^|:)rl:geography:route:", key)
            self.assertIsNone(FLOAT.search(key))
            value = pickle.loads(cache._cache[key])
            self.assertIsInstance(value, int)

    def test_the_module_writes_no_model_no_cache_and_no_file(self):
        from pathlib import Path

        source = Path(routing.__file__).read_text(encoding="utf-8")
        for word in ("cache.set", "objects.create", ".save(", " open(", "write_text",
                     "LineStringField"):
            self.assertNotIn(word, source)


class BilledTests(RouteTestCase):
    billed = True

    def test_only_the_charge_tables_grow_and_none_holds_the_route(self):

        def counts():
            return {model._meta.label: model.objects.count() for model in apps.get_models()}

        def rows(labels):
            out = []
            for model in apps.get_models():
                if model._meta.label in labels:
                    for row in model.objects.all():
                        out.append((model._meta.label, {field.name: getattr(row, field.attname)
                                                        for field in row._meta.concrete_fields}))
            return out

        with answering():   # the wallet and its accounts are opened by a first charge
            post(self.client, self.url, body(to={"lat": 50.0614, "lng": 19.9366}))
        before = counts()
        before_rows = {repr(row) for row in rows(set(before))}
        with answering():
            self.assertEqual(post(self.client, self.url, body()).status_code, 200)
        after = counts()
        grown = {label for label in after if after[label] != before.get(label, 0)}
        allowed = {"geography.GeographyUsageEvent", "tariffs.UsageRecord", "tariffs.UsageCharge",
                   "assets.LedgerTransaction", "assets.LedgerEntry", "assets.LedgerHash",
                   "audit.AuditRecord"}
        self.assertEqual(grown - allowed, set())
        self.assertIn("geography.GeographyUsageEvent", grown)
        if apps.is_installed("toto.audit"):
            from toto.audit.models import AuditRecord

            self.assertEqual(after["audit.AuditRecord"] - before["audit.AuditRecord"], 1)
            records = AuditRecord.objects.filter(action="GEOGRAPHY.ROUTE")
            self.assertEqual(records.count(), 2)
            for record in records:
                self.assertEqual((record.object_id, record.object_type), ("", ""))
                self.assertEqual(set(record.metadata), {"metric", "amount", "outcome"})
        secrets = ("52.2297", "21.0122", "54.352", "18.6466", "20.1234", "53.4321",
                   "339.51", "339512", "205.8", "12345.6", "LineString")
        for label, row in rows(grown):
            text = repr((label, row))
            if text in before_rows:
                continue
            for secret in secrets:
                self.assertNotIn(secret, text, label)

    def test_a_route_costs_one_compute_mana_once(self):
        from decimal import Decimal

        economy = self.economy
        key = op()
        before = economy.held(self.user, "compute")
        with answering():
            post(self.client, self.url, body(op=key))
            post(self.client, self.url, body(op=key))
        self.assertEqual(before - economy.held(self.user, "compute"), Decimal("1"))
        self.assertEqual(economy.charges("geography.route"), 1)

    def test_a_failed_router_costs_nothing(self):
        economy = self.economy
        before = economy.held(self.user, "compute")
        with mock.patch(URLOPEN, side_effect=URLError("down")), \
                self.assertLogs("toto.geography.routing", "WARNING"):
            post(self.client, self.url, body())
        self.assertEqual(economy.held(self.user, "compute"), before)
        self.assertEqual(economy.charges(), 0)
