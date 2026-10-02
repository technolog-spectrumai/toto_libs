"""A home pin follows its person's sharing switch on the Locations map too
(2026-10-01, 37c.21; ``access.without_private_homes``).

The People map kept to the switch, but the Locations map, its JSON, its
pickers and its pages listed every address — a member's home pin among them,
unnamed, with its exact point and any street looked up — to every member who
could open Locations, whatever the switch said.

Each person's switch rules their own pin only (37c.32): a household's one
address shows when one resident shares it exactly. Until then another
resident's Off hid the pin somebody chose to share.

    manage.py test toto.locations.tests_home_pins
"""

from unittest import mock, skipUnless

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.locations import access
from toto.locations.models import HAS_GIS, Address
from toto.people.models import LocationSharing, Person

User = get_user_model()


class HomePinCase(TestCase):
    def setUp(self):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        self.ann_user = User.objects.create_user("ann", password="pw")
        self.home = Address.objects.create(street="Hidden Lane", building="7",
                                           locality_name="Town", latitude=52.2297,
                                           longitude=21.0122)
        self.ann = Person.objects.create(user=self.ann_user, display_name="Ann",
                                         address=self.home)
        self.shop = Address.objects.create(street="Market Square", locality_name="Town",
                                           latitude=52.25, longitude=21.0)
        self.viewer = User.objects.create_user("viewer", password="pw")
        Person.objects.create(user=self.viewer, display_name="Viewer")
        self.root = User.objects.create_superuser("root", "root@example.com", "pw")

    def share(self, how):
        Person.objects.filter(pk=self.ann.pk).update(location_sharing=how)

    def readable(self, user):
        return set(access.readable_addresses(user))


class TheRuleTests(HomePinCase):
    def test_off_keeps_the_pin_from_everybody_else(self):
        for user in (self.viewer, self.root, None):
            with self.subTest(user=user):
                self.assertEqual(self.readable(user), {self.shop})
                self.assertFalse(access.may_read(user, self.home))

    def test_the_person_always_sees_their_own(self):
        self.assertEqual(self.readable(self.ann_user), {self.shop, self.home})
        self.assertTrue(access.may_read(self.ann_user, self.home))

    def test_approximate_is_the_people_maps_and_not_this_maps(self):
        self.share(LocationSharing.APPROXIMATE)
        self.assertEqual(self.readable(self.viewer), {self.shop})
        self.assertFalse(access.may_read(self.viewer, self.home))

    def test_exact_shows_it(self):
        self.share(LocationSharing.EXACT)
        self.assertEqual(self.readable(self.viewer), {self.shop, self.home})
        self.assertTrue(access.may_read(self.viewer, self.home))



class OneHouseholdTests(HomePinCase):
    """37c.32: Ann and Bob live at one address (one ``Address`` row)."""

    def setUp(self):
        super().setUp()
        self.bob_user = User.objects.create_user("bob", password="pw")
        self.bob = Person.objects.create(user=self.bob_user, display_name="Bob",
                                         address=self.home)

    def bob_shares(self, how):
        Person.objects.filter(pk=self.bob.pk).update(location_sharing=how)

    def test_one_residents_exact_share_shows_it_whatever_the_other_chose(self):
        self.share(LocationSharing.EXACT)
        for bob in (LocationSharing.OFF, LocationSharing.APPROXIMATE):
            with self.subTest(bob=bob):
                self.bob_shares(bob)
                self.assertEqual(self.readable(self.viewer), {self.shop, self.home})
                self.assertTrue(access.may_read(self.viewer, self.home))

    def test_either_resident_may_be_the_one_who_shares(self):
        self.bob_shares(LocationSharing.EXACT)
        self.assertEqual(self.readable(self.viewer), {self.shop, self.home})
        self.assertTrue(access.may_read(self.viewer, self.home))

    def test_nobody_sharing_it_keeps_it_from_everybody_but_the_residents(self):
        self.share(LocationSharing.OFF)
        self.bob_shares(LocationSharing.APPROXIMATE)
        for user in (self.viewer, self.root, None):
            with self.subTest(user=user):
                self.assertEqual(self.readable(user), {self.shop})
                self.assertFalse(access.may_read(user, self.home))
        for resident in (self.ann_user, self.bob_user):
            with self.subTest(resident=resident):
                self.assertEqual(self.readable(resident), {self.shop, self.home})
                self.assertTrue(access.may_read(resident, self.home))

    def test_the_people_map_still_shows_only_who_shares(self):
        from toto.locations import people_access

        self.share(LocationSharing.EXACT)
        shown = set(people_access.shared_people(self.viewer).values_list("pk", flat=True))
        self.assertIn(self.ann.pk, shown)
        self.assertNotIn(self.bob.pk, shown)
        self.assertFalse(people_access.may_see_location(self.viewer, self.bob))

    def test_the_map_page_shows_the_shared_pin(self):
        self.share(LocationSharing.EXACT)
        self.client.force_login(self.viewer)
        self.assertContains(self.client.get(reverse("locations:locations_all")), "Hidden Lane")
        detail = self.client.get(reverse("locations:location_detail",
                                         kwargs={"kind": "address", "pk": self.home.pk}))
        self.assertEqual(detail.status_code, 200)


class TheDoorsTests(HomePinCase):
    def test_the_map_page_and_the_detail_page_keep_it(self):
        self.client.force_login(self.viewer)
        page = self.client.get(reverse("locations:locations_all"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Hidden Lane")
        self.assertContains(page, "Market Square")
        detail = self.client.get(reverse("locations:location_detail",
                                         kwargs={"kind": "address", "pk": self.home.pk}))
        self.assertEqual(detail.status_code, 404)

    def test_the_owner_still_finds_their_pin_on_the_map(self):
        self.client.force_login(self.ann_user)
        self.assertContains(self.client.get(reverse("locations:locations_all")), "Hidden Lane")


class TheEventsPickerTests(HomePinCase):
    """The events' place picker asks the same rule (2026-10-01, the review of
    stage 37c): the new-event form and the desktop app's form data listed
    every address — a home pin shared with nobody among them — and the API
    put an event at any address it was given."""

    def setUp(self):
        super().setUp()
        from django.contrib.auth.models import Group

        self.viewer.groups.add(Group.objects.get_or_create(name="data_mesh")[0])
        self.client.force_login(self.viewer)

    def test_the_new_event_form_offers_no_hidden_pin(self):
        page = self.client.get(reverse("events:event_create"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Hidden Lane")
        self.assertContains(page, "Market Square")

    def test_a_hidden_pin_posted_as_the_place_is_refused(self):
        from toto.events.models import ScheduledEvent

        response = self.client.post(reverse("events:event_create"), {
            "title": "At Ann's", "description": "",
            "start_time_0": "2026-11-02", "start_time_1": "10:00",
            "end_time_0": "2026-11-02", "end_time_1": "12:00",
            "address": self.home.pk, "public": "on"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("address", response.context["form"].errors)
        self.assertFalse(ScheduledEvent.objects.filter(title="At Ann's").exists())

    def test_a_readable_place_is_still_taken(self):
        from toto.events.models import ScheduledEvent

        response = self.client.post(reverse("events:event_create"), {
            "title": "At the market", "description": "",
            "start_time_0": "2026-11-02", "start_time_1": "10:00",
            "end_time_0": "2026-11-02", "end_time_1": "12:00",
            "address": self.shop.pk, "public": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(ScheduledEvent.objects.get(title="At the market").address, self.shop)

    def test_the_apps_form_data_keeps_it(self):
        body = self.client.get(reverse("events:api_form_data")).json()
        shown = {a["id"] for a in body["addresses"]}
        self.assertIn(self.shop.pk, shown)
        self.assertNotIn(self.home.pk, shown)

    def test_the_api_puts_no_event_at_it(self):
        import json

        from toto.events.models import ScheduledEvent

        response = self.client.post(
            reverse("events:api_list"), json.dumps({
                "title": "At Ann's", "start_time": "2026-11-02T10:00:00",
                "end_time": "2026-11-02T12:00:00", "address_id": self.home.pk}),
            content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "Address not found."})
        self.assertFalse(ScheduledEvent.objects.filter(title="At Ann's").exists())

    def test_the_person_may_still_hold_an_event_at_home(self):
        self.client.force_login(self.ann_user)
        self.assertContains(self.client.get(reverse("events:event_create")), "Hidden Lane")


@skipUnless(HAS_GIS, "routes draw on geometry; their pages 404 without GIS")
class RoutePageTests(HomePinCase):
    """A route's page names its ends by the rule its JSON keeps (2026-10-02,
    crown 41): the JSON went through ``readable_or_none`` and left a hidden
    home out, while the page printed the street from the relation and linked
    to it — to every member who may read the route. Ann saved a route from
    her home; her switch is Off."""

    def setUp(self):
        super().setUp()
        from django.contrib.gis.geos import LineString, MultiLineString

        from toto.locations.models import Route

        self.route = Route.objects.create(
            name="Morning walk", created_by=self.ann_user,
            geometry=MultiLineString(LineString((21.0122, 52.2297), (21.0, 52.25)), srid=4326),
            start_address=self.home, end_address=self.shop)

    def page(self, user):
        self.client.force_login(user)
        response = self.client.get(reverse("locations:route_detail", args=[self.route.pk]))
        self.assertEqual(response.status_code, 200)
        return response

    def test_the_page_keeps_a_hidden_home_from_everybody_else(self):
        for user in (self.viewer, self.root):
            with self.subTest(user=user.username):
                page = self.page(user)
                self.assertNotContains(page, "Hidden Lane")
                self.assertNotContains(page, reverse("locations:address_detail",
                                                     args=[self.home.pk]))
                self.assertContains(page, "Market Square")
                self.assertContains(page, "A private address")

    def test_the_person_and_an_exact_share_still_name_it(self):
        self.assertContains(self.page(self.ann_user), "Hidden Lane")
        self.share(LocationSharing.EXACT)
        self.assertContains(self.page(self.viewer), "Hidden Lane")

    def test_an_end_that_was_never_set_still_says_so(self):
        from toto.locations.models import Route

        Route.objects.filter(pk=self.route.pk).update(start_address=None)
        page = self.page(self.viewer)
        self.assertContains(page, "Not set")
        self.assertNotContains(page, "A private address")

    def test_the_zone_pages_route_list_keeps_it_too(self):
        """The zone page lists its routes "start → end" the same way (a host
        with toto.kanban fills it; the rows are drawn here as it would)."""
        from django.template.loader import render_to_string

        from toto.locations import views
        from toto.locations.models import Zone
        from toto.ui import PageProcessor

        zone = Zone.objects.create(name="Centre", geometry="MULTIPOLYGON(((0 0, 1 0, 1 1, 0 1, 0 0)))")
        for user, shown in ((self.viewer, False), (self.ann_user, True)):
            with self.subTest(user=user.username):
                request = RequestFactory().get("/")
                request.user = user
                html = render_to_string("locations/zone_detail.html", PageProcessor().decorate({
                    "zone": zone, "zone_payload": {}, "campaigns": [], "missions": [],
                    "addresses": [], "routes": [self.route],
                    "route_rows": views.route_rows(user, [self.route]),
                }, request), request=request)
                self.assertEqual("Hidden Lane" in html, shown)
                self.assertIn("Market Square", html)


@skipUnless(HAS_GIS, "route search draws on geometry; it 404s without GIS")
class RouteNameTests(HomePinCase):
    """The route search's default name is never built from a home pin's
    label (2026-10-02, crown 41). It is saved with the route and shown to
    every member who may read the route — after its person stops sharing
    too — so a route from Ann's home was called "Hidden Lane 7, Town → …"."""

    FEATURE = {"type": "Feature", "properties": {"mode": "car", "distance_km": 3.1,
                                                 "duration_min": 8.0},
               "geometry": {"type": "LineString", "coordinates": [[21.0122, 52.2297],
                                                                  [21.0, 52.25]]}}

    def search(self, user, start, end):
        self.client.force_login(user)
        with mock.patch("toto.locations.views.fetch_traversable_route",
                        return_value=self.FEATURE):
            response = self.client.get(reverse("locations:route_search"), {
                "mode": "car", "start_address": start.pk, "end_address": end.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["error"], "")
        return response

    def test_a_home_pin_end_is_named_a_private_address(self):
        page = self.search(self.ann_user, self.home, self.shop)
        self.assertEqual(page.context["route_name"], "A private address → Market Square, Town")
        self.assertNotContains(page, 'value="Hidden Lane')
        # Ann's own map still marks her end with what she reads.
        self.assertEqual(page.context["point_labels"]["start"], "Hidden Lane 7, Town")

    def test_a_shared_home_pin_is_not_named_either(self):
        self.share(LocationSharing.EXACT)
        page = self.search(self.viewer, self.shop, self.home)
        self.assertEqual(page.context["route_name"], "Market Square, Town → A private address")

    def test_other_addresses_still_name_the_route(self):
        other = Address.objects.create(street="Long Street", building="3", locality_name="Town",
                                       latitude=52.24, longitude=21.01)
        page = self.search(self.viewer, self.shop, other)
        self.assertEqual(page.context["route_name"], "Market Square, Town → Long Street 3, Town")
