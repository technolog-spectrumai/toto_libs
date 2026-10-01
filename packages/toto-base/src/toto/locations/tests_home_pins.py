"""A home pin follows its person's sharing switch on the Locations map too
(2026-10-01, 37c.21; ``access.without_private_homes``).

The People map kept to the switch, but the Locations map, its JSON, its
pickers and its pages listed every address — a member's home pin among them,
unnamed, with its exact point and any street looked up — to every member who
could open Locations, whatever the switch said.

    manage.py test toto.locations.tests_home_pins
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.locations import access
from toto.locations.models import Address
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

    def test_a_pin_two_people_share_needs_both_to_share_it(self):
        self.share(LocationSharing.EXACT)
        Person.objects.create(user=User.objects.create_user("bob", password="pw"),
                              display_name="Bob", address=self.home)
        self.assertFalse(access.may_read(self.viewer, self.home))


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
