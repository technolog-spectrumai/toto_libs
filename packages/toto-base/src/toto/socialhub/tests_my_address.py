"""Setting your own address: the write door the sharing switch always lacked.

`set_location_sharing` decided WHO may see an address; until now nothing in
the suite could WRITE one, so the setting sat there with nothing to share.
These pin the new door: own profile only by construction, updated in place,
range-checked, and the search endpoint gated on the host's geocoding config.
"""

from __future__ import annotations

from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.locations.models import Address
from toto.people.models import Person

User = get_user_model()

GEOCODING_OFF = {"enabled": False}


class AddressTestCase(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="Tests",
                                publication_year=2026, active=True)
        self.user = User.objects.create_user("pinner", password="pw")
        self.client.force_login(self.user)
        self.url = reverse("socialhub:set_my_address")


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
        with mock.patch("toto.locations.geocode.urlopen") as opened:
            self.client.post(self.url, {"latitude": "52", "longitude": "21"})
        opened.assert_not_called()


@override_settings(LOCATIONS_GEOCODING={"enabled": True})
class ReverseGeocodeTests(AddressTestCase):
    def test_the_saved_address_is_humanised_when_the_host_allows(self):
        resolved = {"country_name": "PL", "locality_name": "Warszawa",
                    "street": "Nowy Świat"}
        with mock.patch("toto.socialhub.views.profile."
                        "reverse_geocode_address", create=True) as rev, \
             mock.patch("toto.locations.geocode.reverse_geocode_address",
                        return_value=resolved):
            self.client.post(self.url, {"latitude": "52.23",
                                        "longitude": "21.01"})
        address = self.user.community_profile.address
        self.assertEqual(address.locality_name, "Warszawa")
        self.assertEqual(address.latitude, 52.23)


class SearchAddressTests(AddressTestCase):
    @override_settings(LOCATIONS_GEOCODING=GEOCODING_OFF)
    def test_the_search_door_is_closed_where_geocoding_is_off(self):
        """The same gate placidia's boundary pins: a host that makes no
        outbound calls answers 404 and renders no search box."""
        response = self.client.get(reverse("socialhub:search_address"),
                                   {"q": "Warszawa"})
        self.assertEqual(response.status_code, 404)

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_a_short_query_searches_nothing(self):
        with mock.patch("toto.locations.geocode.urlopen") as opened:
            response = self.client.get(reverse("socialhub:search_address"),
                                       {"q": "ab"})
        self.assertEqual(response.json(), {"results": []})
        opened.assert_not_called()

    @override_settings(LOCATIONS_GEOCODING={"enabled": True})
    def test_results_come_back_in_the_geocode_contract(self):
        hits = [{"label": "Warszawa, Polska", "name": "Warszawa",
                 "lat": "52.23", "lng": "21.01", "type": "city",
                 "country_name": "PL", "state_or_province_name": "",
                 "locality_name": "Warszawa"}]
        with mock.patch("toto.socialhub.views.profile."
                        "forward_geocode_locations", create=True), \
             mock.patch("toto.locations.geocode.forward_geocode_locations",
                        return_value=hits):
            response = self.client.get(reverse("socialhub:search_address"),
                                       {"q": "Warszawa"})
        self.assertEqual(response.json()["results"][0]["lat"], "52.23")


@override_settings(LOCATIONS_GEOCODING=GEOCODING_OFF)
class PageTests(AddressTestCase):
    def test_the_own_profile_page_offers_the_picker(self):
        person = Person.objects.create(user=self.user, display_name="Pinner")
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]))
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
                                           args=[person.slug]))
        self.assertNotContains(response, "Set my address")
        self.assertNotContains(response, "address-pick-map")

    def test_with_an_address_the_button_says_modify(self):
        person = Person.objects.create(
            user=self.user, display_name="Pinner",
            address=Address.objects.create(latitude=52.0, longitude=21.0))
        response = self.client.get(reverse("socialhub:profile_details",
                                           args=[person.slug]))
        self.assertContains(response, "Modify my address")
