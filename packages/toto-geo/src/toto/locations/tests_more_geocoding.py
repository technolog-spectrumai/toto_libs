"""Geocoding's charge paths at their edges (2026-09-28): a cache that is down,
a provider that moved, a point asked twice a hair apart, a usage event the
quota already holds. The provider is `urlopen`, patched as in
`tests_geocoding`; nothing reaches the network.
"""

from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from toto.locations import billing, geocoding
from toto.locations.geocoding import GeocodingError
from toto.locations.tests_geocoding import DLUGA, GDANSK, URLOPEN, ServiceTestCase, answering


class FakeCache:
    """A dict standing in for the cache, so a test can read the keys."""

    def __init__(self):
        self.store = {}

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value, timeout=None):
        self.store[key] = value


class CacheEdgeTests(ServiceTestCase):
    def test_a_cache_that_is_down_is_a_miss_and_the_answer_is_still_charged(self):
        broken = mock.Mock(**{"get.side_effect": ConnectionError("down"),
                              "set.side_effect": ConnectionError("down")})
        with mock.patch.object(geocoding, "cache", broken), answering(GDANSK), \
                self.assertLogs("toto.locations.geocoding", "WARNING") as logged:
            answer = geocoding.search_answer(self.user, "Gdansk")
        self.assertFalse(answer.cached)
        self.assertEqual(answer.value[0]["name"], "Gdańsk")
        self.assertEqual(self.events().count(), 1)
        self.assertEqual(len(logged.records), 2)                   # the miss and the unkept answer

    def test_an_answer_the_cache_could_not_keep_is_asked_for_again(self):
        broken = mock.Mock(**{"get.return_value": geocoding._MISS,
                              "set.side_effect": ConnectionError("down")})
        with mock.patch.object(geocoding, "cache", broken), \
                self.assertLogs("toto.locations.geocoding", "WARNING"):
            with answering(GDANSK) as first:
                geocoding.search(self.user, "Gdansk")
            with answering(GDANSK) as second:
                geocoding.search(self.user, "Gdansk")
        first.assert_called_once()
        second.assert_called_once()
        self.assertEqual(self.events().count(), 2)

    def test_the_cache_never_learns_what_was_looked_up(self):
        fake = FakeCache()
        with mock.patch.object(geocoding, "cache", fake), answering(GDANSK):
            geocoding.search(self.user, "Secret Street 7")
        with mock.patch.object(geocoding, "cache", fake), answering(DLUGA):
            geocoding.reverse(self.user, 54.35123, 18.65456)
        self.assertEqual(len(fake.store), 2)
        for key in fake.store:
            with self.subTest(key=key):
                self.assertNotIn("Secret", key)
                self.assertNotIn("54.35", key)
                self.assertRegex(key, r"^locations:geocode:(search|reverse):[0-9a-f]{64}$")

    def test_a_point_a_hair_away_is_the_same_doorstep(self):
        with answering(DLUGA):
            first = geocoding.reverse_answer(self.user, 54.351231, 18.654561)
        with mock.patch(URLOPEN) as asked:
            second = geocoding.reverse_answer(self.user, 54.351229, 18.654559)
        asked.assert_not_called()
        self.assertEqual((first.cached, second.cached), (False, True))
        self.assertEqual(self.events().count(), 2)

    def test_a_host_that_moves_provider_asks_the_new_one(self):
        with answering(GDANSK):
            geocoding.search(self.user, "Gdansk")
        moved = {"enabled": True, "search_url": "https://geo.example/search"}
        with override_settings(LOCATIONS_GEOCODING=moved), answering(GDANSK) as asked:
            answer = geocoding.search_answer(self.user, "Gdansk")
        self.assertFalse(answer.cached)
        self.assertTrue(asked.call_args.args[0].full_url.startswith("https://geo.example/search?"))


class ChargeEdgeTests(ServiceTestCase):
    def test_a_usage_event_the_quota_already_holds_is_not_charged_again(self):
        with mock.patch("toto.locations.billing.record_usage", return_value=None), \
                mock.patch("toto.locations.billing.charge") as charged:
            self.assertFalse(billing.settle_lookup(self.user, geocoding.SEARCH_LABEL))
        charged.assert_not_called()

    def test_each_lookup_is_its_own_source(self):
        with mock.patch("toto.locations.billing.charge") as charged:
            billing.settle_lookup(self.user, geocoding.SEARCH_LABEL)
            billing.settle_lookup(self.user, geocoding.SEARCH_LABEL)
        sources = {c.kwargs["source_id"] for c in charged.call_args_list}
        self.assertEqual(len(sources), 2)
        self.assertEqual({c.kwargs["source_type"] for c in charged.call_args_list},
                         {"locations.geocode"})
        self.assertEqual(self.events().count(), 2)

    def test_nobody_is_checked_for_funds_before_signing_in(self):
        with mock.patch("toto.locations.billing.check_affordable") as checked:
            for user in (None, AnonymousUser()):
                with self.subTest(user=user), self.assertRaises(GeocodingError) as caught:
                    geocoding.check_affordable(user, 2)
                self.assertEqual(caught.exception.status_code, 403)
        checked.assert_not_called()

    def test_check_affordable_asks_billing_for_the_whole_batch(self):
        with mock.patch("toto.locations.billing.check_affordable") as checked:
            geocoding.check_affordable(self.user, 2)
        checked.assert_called_once_with(self.user, 2)

    def test_a_billing_refusal_on_reverse_asks_nobody_and_records_nothing(self):
        from toto.quota.charge import InsufficientFunds

        with mock.patch("toto.locations.billing.check_funds",
                        side_effect=InsufficientFunds("RED", 1, 0)), mock.patch(URLOPEN) as asked, \
                self.assertRaises(InsufficientFunds):
            geocoding.reverse(self.user, 54.35, 18.65)
        asked.assert_not_called()
        self.assertEqual(self.events().count(), 0)

    def test_a_point_that_is_not_a_number_is_refused_uncharged(self):
        for lat, lng in (("north", 1), (None, 1), ("nan", 0), ("inf", 0), (0, 181)):
            with self.subTest(lat=lat, lng=lng), mock.patch(URLOPEN) as asked:
                with self.assertRaises(GeocodingError) as caught:
                    geocoding.reverse(self.user, lat, lng)
                self.assertEqual(caught.exception.status_code, 400)
                asked.assert_not_called()
        self.assertEqual(self.events().count(), 0)
