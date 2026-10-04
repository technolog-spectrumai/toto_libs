"""The pages that offer a place lookup ask for it, and never on their own.

2026-09-28. A place lookup is charged, so opening the map or the new-address
form asks the provider nothing: the map's web search is a POST sent on Enter
or its Search button, and the address form's "Fill from the map point" is a
button. Both carry the price (`{% price_hint "locations.geocode" %}`) and
disappear on a host with geocoding off. The provider's `urlopen` is patched
in every test that opens a page, so a lookup that slipped back into a GET
fails here rather than reaching the network.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.locations.models import HAS_GIS, Address

User = get_user_model()

ON = {"enabled": True}
OFF = {"enabled": False}
URLOPEN = "toto.locations.geocode.urlopen"


class PageTestCase(TestCase):
    def setUp(self):
        if not HAS_GIS:
            self.skipTest("the locations pages 404 without GIS")
        Platform.objects.get_or_create(
            active=True, defaults={"site_name": "T", "author": "t", "publication_year": 2026})
        cache.clear()
        self.user = User.objects.create_user("ada", password="x")
        self.client.force_login(self.user)

    def open(self, url, params=None):
        with mock.patch(URLOPEN) as asked:
            response = self.client.get(url, params or {})
        asked.assert_not_called()
        self.assertEqual(response.status_code, 200)
        return response


def priced(test):
    """This host's own rate card, where it has one: a lookup at 0.5 compute
    mana. Skips on a host that bills nothing."""
    from django.apps import apps

    if not (apps.is_installed("toto.tariffs") and apps.is_installed("toto.mana")):
        test.skipTest("this host bills nothing")
    from toto.mana.tests import fixtures

    master = override_settings(**fixtures.MASTER)
    master.enable()
    test.addCleanup(master.disable)
    fixtures.economy()
    fixtures.seed_prices()


@override_settings(LOCATIONS_GEOCODING=ON)
class MapSearchTests(PageTestCase):
    def test_opening_the_map_asks_nobody(self):
        Address.objects.create(street="Długa", locality_name="Gdańsk",
                               latitude=54.349, longitude=18.653)
        self.open(reverse("locations:locations_all"))

    def test_the_web_search_is_a_button_posting_to_the_charged_door(self):
        page = self.open(reverse("locations:locations_all"))
        self.assertContains(page, 'data-testid="map-web-search"')
        self.assertContains(page, f'window.geocodeSearchUrl = "{reverse("locations:geocode_search")}"')
        self.assertContains(page, "window.geocodingEnabled = true")
        self.assertContains(page, '"X-CSRFToken": window.locationsCsrfToken')
        self.assertContains(page, '@keydown.enter.prevent="searchPlaces()"')

    def test_nothing_searches_the_web_as_you_type(self):
        """The old page fetched `?q=` 350 ms after every keystroke; the watch
        on the box now only retires the last answer."""
        page = self.open(reverse("locations:locations_all")).content.decode()
        self.assertNotIn("?q=", page)
        self.assertNotIn(reverse("locations:location_search_api"), page)
        watch = page[page.index('this.$watch("mapSearch"'):]
        self.assertNotIn("fetch(", watch[:watch.index("});")])

    def test_the_best_match_is_jumped_to_and_the_list_kept(self):
        page = self.open(reverse("locations:locations_all"))
        self.assertContains(page, "this.moveMapToLocation(this.mapSearchRemoteResults[0], { keepOpen: true })")
        self.assertContains(page, "if (seq !== this.mapSearchSeq)")

    def test_the_map_follows_dark_mode(self):
        self.assertContains(self.open(reverse("locations:locations_all")), "window.totoTileLayer(map)")

    def test_the_button_carries_the_price(self):
        self.assertNotContains(self.open(reverse("locations:locations_all")), "data-mana-role",
                               msg_prefix="unpriced, the hint says nothing")
        priced(self)
        page = self.open(reverse("locations:locations_all"))
        self.assertContains(page, 'data-mana-role="compute"')
        self.assertContains(page, "0.5")

    @override_settings(LOCATIONS_GEOCODING=OFF)
    def test_a_host_without_geocoding_offers_no_web_search(self):
        page = self.open(reverse("locations:locations_all"))
        self.assertNotContains(page, 'data-testid="map-web-search"')
        self.assertContains(page, "window.geocodingEnabled = false")


@override_settings(LOCATIONS_GEOCODING=ON)
class AddressFormTests(PageTestCase):
    url = "locations:address_create"

    def test_a_map_click_prefills_the_point_and_asks_nobody(self):
        page = self.open(reverse(self.url), {"lat": "54.35", "lng": "18.65"})
        self.assertContains(page, 'value="54.35"')
        self.assertContains(page, 'value="18.65"')
        self.assertEqual(page.context["form"].initial, {})

    def test_named_fields_from_the_link_still_prefill(self):
        page = self.open(reverse(self.url), {"street": "Długa", "locality": "Gdańsk"})
        self.assertEqual(page.context["form"].initial,
                         {"street": "Długa", "locality_name": "Gdańsk"})

    def test_filling_from_the_point_is_a_button_posting_to_the_charged_door(self):
        page = self.open(reverse(self.url), {"lat": "54.35", "lng": "18.65"})
        self.assertContains(page, 'data-testid="fill-from-point"')
        self.assertContains(page, reverse("locations:geocode_reverse"))
        self.assertContains(page, "!input.value.trim()")

    def test_the_button_carries_the_price(self):
        priced(self)
        page = self.open(reverse(self.url))
        self.assertContains(page, 'data-mana-role="compute"')

    @override_settings(LOCATIONS_GEOCODING=OFF)
    def test_a_host_without_geocoding_offers_no_fill(self):
        page = self.open(reverse(self.url), {"lat": "54.35", "lng": "18.65"})
        self.assertNotContains(page, 'data-testid="fill-from-point"')

    def test_saving_asks_nobody_and_keeps_the_point(self):
        with mock.patch(URLOPEN) as asked:
            response = self.client.post(reverse(self.url), {
                "country_name": "pl", "locality_name": "Gdańsk", "street": "Długa",
                "building": "1", "latitude": "54.35", "longitude": "18.65"})
        asked.assert_not_called()
        self.assertEqual(response.status_code, 302)
        address = Address.objects.get()
        self.assertEqual((address.country_name, address.latitude), ("PL", 54.35))
        self.assertEqual(address.created_by, self.user)


@override_settings(LOCATIONS_GEOCODING=ON)
class RoutePageTests(PageTestCase):
    def test_search_route_carries_the_price_of_a_typed_place(self):
        priced(self)
        page = self.open(reverse("locations:route_search"))
        self.assertContains(page, 'data-testid="route-lookup-price"')
        self.assertContains(page, 'data-mana-role="compute"')

    def test_the_route_map_follows_dark_mode(self):
        self.assertContains(self.open(reverse("locations:route_search")),
                            "window.totoTileLayer(map)")
