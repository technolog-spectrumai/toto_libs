"""Setting your own address: the write door the sharing switch always lacked.

`set_location_sharing` decided WHO may see an address; until now nothing in
the suite could WRITE one, so the setting sat there with nothing to share.
These pin the new door: own profile only by construction, updated in place,
range-checked, and the search endpoint gated on the host's geocoding config.

Since 2026-09-28 both lookups are charged place lookups
(`toto.locations.geocoding`): the name search is a POST, and the street and
town come only from "Save and look up the address" — a plain Save asks
nobody. The provider is patched at its `urlopen`, so these walk the real
service: one usage event per answer, none for a refusal.
"""

from __future__ import annotations

from unittest import mock
from urllib.error import URLError

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.locations.models import Address, LocationsUsageEvent
from toto.locations.tests_geocoding import DLUGA, GDANSK, SEA, answering, unthrottled
from toto.people.models import Person

User = get_user_model()

GEOCODING_OFF = {"enabled": False}
URLOPEN = "toto.locations.geocode.urlopen"


class AddressTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        # The lookup cache and both throttles live in the cache.
        cache.clear()
        unthrottled(self)
        self.user = User.objects.create_user("pinner", password="pw")
        self.client.force_login(self.user)
        self.url = reverse("socialhub:set_my_address")

    def lookups(self):
        return LocationsUsageEvent.objects.filter(user=self.user).count()


@override_settings(LOCATIONS_GEOCODING=GEOCODING_OFF)
class SetMyAddressTests(AddressTestCase):
    def test_saving_creates_the_profile_and_the_address(self):
        response = self.client.post(self.url, {
            "latitude": "52.2297", "longitude": "21.0122"})
        self.assertEqual(response.status_code, 302)
        profile = self.user.community_profile
        self.assertEqual(profile.display_name, "pinner")
        self.assertEqual(profile.address.latitude, 52.2297)

    def test_resaving_moves_the_pin_in_place(self):
        """The Address row is updated, never replaced — nothing referencing
        it dangles, and the table does not grow with every correction."""
        self.client.post(self.url, {"latitude": "52.0", "longitude": "21.0"})
        before = Address.objects.count()
        self.client.post(self.url, {"latitude": "50.0", "longitude": "19.0"})
        self.assertEqual(Address.objects.count(), before)
        self.user.refresh_from_db()
        self.assertEqual(self.user.community_profile.address.latitude, 50.0)

    def test_a_pin_off_the_earth_is_refused(self):
        response = self.client.post(self.url, {
            "latitude": "91", "longitude": "0"}, follow=True)
        self.assertContains(response, "Place the pin on the map first.")
        self.assertFalse(Person.objects.filter(user=self.user).exists())

    def test_garbage_coordinates_are_refused(self):
        response = self.client.post(self.url, {
            "latitude": "here", "longitude": "there"}, follow=True)
        self.assertContains(response, "Place the pin on the map first.")

    def test_get_writes_nothing(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Address.objects.count(), 0)

    def test_login_is_required(self):
        self.client.logout()
        response = self.client.post(self.url, {
            "latitude": "52", "longitude": "21"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_only_your_own_pin_no_matter_what_is_posted(self):
        """There is no way to name another person: the door writes
        request.user's profile, full stop."""
        other = User.objects.create_user("victim", password="pw")
        victim = Person.objects.create(user=other, display_name="Victim")
        self.client.post(self.url, {
            "latitude": "1", "longitude": "1",
            "person": victim.pk, "user": other.pk, "profile": victim.pk})
        victim.refresh_from_db()
        self.assertIsNone(victim.address)
        self.assertIsNotNone(self.user.community_profile.address)

    def test_with_geocoding_off_no_outbound_call_is_made(self):
        """Even asked to look the address up: the pin is saved alone and
        the member is told why."""
        with mock.patch(URLOPEN) as opened:
            response = self.client.post(self.url, {
                "latitude": "52", "longitude": "21", "lookup_address": "1"},
                follow=True)
        opened.assert_not_called()
        self.assertEqual(self.user.community_profile.address.latitude, 52.0)
        self.assertContains(response, "was not looked up")
        self.assertEqual(self.lookups(), 0)


@override_settings(LOCATIONS_GEOCODING={"enabled": True})
class ReverseGeocodeTests(AddressTestCase):
    def test_a_plain_save_asks_nobody(self):
        """Looking the street up costs a lookup, so it is its own button;
        Save keeps the pin alone."""
        with mock.patch(URLOPEN) as opened:
            self.client.post(self.url, {"latitude": "54.35", "longitude": "18.65"})
        opened.assert_not_called()
        self.assertEqual(self.lookups(), 0)
        self.assertEqual(self.user.community_profile.address.street, "")

    def test_the_saved_address_is_humanised_when_asked_for(self):
        with answering(DLUGA):
            self.client.post(self.url, {"latitude": "54.35123456",
                                        "longitude": "18.65", "lookup_address": "1"})
        address = self.user.community_profile.address
        self.assertEqual((address.locality_name, address.street, address.building),
                         ("Gdańsk", "Długa", "1"))
        self.assertEqual(address.country_name, "PL")
        # The pin is where it was put, not the lookup's rounded point.
        self.assertEqual(address.latitude, 54.35123456)
        self.assertEqual(self.lookups(), 1)

    def test_a_moved_pin_does_not_keep_the_old_street(self):
        with answering(DLUGA):
            self.client.post(self.url, {"latitude": "54.35", "longitude": "18.65",
                                        "lookup_address": "1"})
        with answering({"display_name": "Sopot", "address": {"town": "Sopot",
                                                             "country_code": "pl"}}):
            self.client.post(self.url, {"latitude": "54.44", "longitude": "18.56",
                                        "lookup_address": "1"})
        address = self.user.community_profile.address
        self.assertEqual((address.locality_name, address.street), ("Sopot", ""))
        self.assertEqual(self.lookups(), 2)

    def test_a_point_with_no_address_saves_the_pin_and_says_so(self):
        with answering(SEA):
            response = self.client.post(self.url, {"latitude": "55", "longitude": "18",
                                                   "lookup_address": "1"}, follow=True)
        self.assertContains(response, "No street address is known at that point.")
        self.assertEqual(self.user.community_profile.address.latitude, 55.0)
        self.assertEqual(self.lookups(), 1, "an answer, even an empty one, is a lookup")

    def test_a_refused_lookup_never_loses_the_pin(self):
        from toto.quota.charge import InsufficientFunds

        with mock.patch("toto.locations.billing.check_funds",
                        side_effect=InsufficientFunds("RED", 1, 0)), \
                mock.patch(URLOPEN) as opened:
            response = self.client.post(self.url, {"latitude": "54.35", "longitude": "18.65",
                                                   "lookup_address": "1"}, follow=True)
        opened.assert_not_called()
        self.assertEqual(self.user.community_profile.address.latitude, 54.35)
        self.assertContains(response, "Your pin is saved, but its address was not looked up")
        self.assertEqual(self.lookups(), 0)

    def test_a_provider_failure_is_not_charged(self):
        with mock.patch(URLOPEN, side_effect=URLError("down")), \
                self.assertLogs("toto.locations.geocode", "WARNING"):
            response = self.client.post(self.url, {"latitude": "54.35", "longitude": "18.65",
                                                   "lookup_address": "1"}, follow=True)
        self.assertContains(response, "this lookup was not charged")
        self.assertEqual(self.lookups(), 0)


class SearchAddressTests(AddressTestCase):
    def setUp(self):
        super().setUp()
        self.search_url = reverse("socialhub:search_address")

    @override_settings(LOCATIONS_GEOCODING=GEOCODING_OFF)
    def test_the_search_door_is_closed_where_geocoding_is_off(self):
        """The same gate placidia's boundary pins: a host that makes no
        outbound calls answers 404 and renders no search box."""
        with mock.patch(URLOPEN) as opened:
            response = self.client.post(self.search_url, {"q": "Warszawa"})
        self.assertEqual(response.status_code, 404)
        opened.assert_not_called()

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_a_get_searches_nothing(self):
        """A search spends mana, so a link or a prefetch must not run one."""
        with mock.patch(URLOPEN) as opened:
            response = self.client.get(self.search_url, {"q": "Warszawa"})
        self.assertEqual(response.status_code, 405)
        opened.assert_not_called()

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_a_short_query_searches_nothing_and_costs_nothing(self):
        with mock.patch(URLOPEN) as opened:
            response = self.client.post(self.search_url, {"q": "a"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        opened.assert_not_called()
        self.assertEqual(self.lookups(), 0)

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_results_come_back_in_the_geocode_contract_for_one_lookup(self):
        with answering(GDANSK):
            response = self.client.post(self.search_url, {"q": "Gdansk"})
        self.assertEqual(response.status_code, 200)
        hit = response.json()["results"][0]
        self.assertEqual((hit["lat"], hit["lng"]), (54.352, 18.6466))
        self.assertEqual(hit["label"], "Gdańsk, województwo pomorskie, Polska")
        self.assertEqual(self.lookups(), 1)

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_a_refusal_answers_its_sentence_with_its_status(self):
        from toto.quota.charge import InsufficientFunds

        with mock.patch("toto.locations.billing.check_funds",
                        side_effect=InsufficientFunds("RED", 1, 0)), \
                mock.patch(URLOPEN) as opened:
            response = self.client.post(self.search_url, {"q": "Gdansk"})
        self.assertEqual(response.status_code, 402)
        self.assertTrue(response.json()["error"])
        opened.assert_not_called()

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_the_search_needs_the_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        with mock.patch(URLOPEN) as opened:
            response = client.post(self.search_url, {"q": "Gdansk"})
        self.assertEqual(response.status_code, 403)
        opened.assert_not_called()


@override_settings(LOCATIONS_GEOCODING=GEOCODING_OFF)
class PageTests(AddressTestCase):
    """The picker is on the Edit profile tab of one's own profile (stage 50);
    on somebody else's profile no tab has it."""

    def test_the_own_profile_page_offers_the_picker(self):
        person = Person.objects.create(user=self.user, display_name="Pinner")
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]) + "?tab=edit")
        self.assertContains(response, "Set my address")
        self.assertContains(response, "address-pick-map")
        # Geocoding is off on this host: no search box renders. Asserted on
        # the template-conditional LABEL — the results-list id also appears
        # in the unconditional script and proves nothing.
        self.assertNotContains(response, "Search for a place")

    def test_someone_elses_page_offers_nothing(self):
        other = User.objects.create_user("other", password="pw")
        person = Person.objects.create(user=other, display_name="Other")
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]) + "?tab=edit")
        self.assertNotContains(response, "Set my address")
        self.assertNotContains(response, "address-pick-map")

    def test_without_geocoding_there_is_nothing_to_pay_for(self):
        person = Person.objects.create(user=self.user, display_name="Pinner")
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]) + "?tab=edit")
        self.assertNotContains(response, 'data-testid="save-and-look-up"')
        self.assertNotContains(response, 'data-testid="address-search-go"')

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_with_geocoding_each_lookup_is_its_own_button(self):
        person = Person.objects.create(user=self.user, display_name="Pinner")
        with mock.patch(URLOPEN) as opened:
            response = self.client.get(reverse("socialhub:profile_details",
                                               args=[person.slug]) + "?tab=edit")
        opened.assert_not_called()
        self.assertContains(response, "Search for a place")
        self.assertContains(response, 'data-testid="address-search-go"')
        self.assertContains(response, 'data-testid="save-and-look-up"')
        # The search posts, with the token, and runs on Enter, not per key.
        self.assertContains(response, "method: 'POST'")
        self.assertContains(response, "'X-CSRFToken'")
        self.assertNotContains(response, "addEventListener('input'")

    def test_the_picker_map_follows_dark_mode(self):
        person = Person.objects.create(user=self.user, display_name="Pinner")
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]) + "?tab=edit")
        self.assertContains(response, "window.totoTileLayer(pick)")

    def test_with_an_address_the_button_says_modify(self):
        person = Person.objects.create(
            user=self.user, display_name="Pinner",
            address=Address.objects.create(latitude=52.0, longitude=21.0))
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]) + "?tab=edit")
        self.assertContains(response, "Modify my address")
