"""The profile setting doors go back where they came from — on this site only
(2026-09-30, stage 31.11).

The language, map-sharing and pin doors answer with a redirect to the
Referer, which is whatever page sent the form: a page elsewhere that posts to
them had them forward the member there. Now a Referer on this host is
followed and anything else falls back to the door's own landing page.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_referer
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from toto.core.models import Platform
from toto.people.models import Person

User = get_user_model()
EVIL = "https://evil.example.com/phish"


class RefererDoorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.user = User.objects.create_user("ada", "ada@example.com", "pw")
        cls.ada = Person.objects.create(user=cls.user, display_name="Ada")

    def setUp(self):
        self.addCleanup(translation.activate, settings.LANGUAGE_CODE)
        self.client.force_login(self.user)

    def post(self, name, data, referer):
        return self.client.post(reverse(f"socialhub:{name}"), data, HTTP_REFERER=referer)

    def test_the_language_door_ignores_an_off_site_referer(self):
        response = self.post("set_preferred_language", {"language": "xx"}, EVIL)
        self.assertEqual(response["Location"],
                         reverse("socialhub:profile_details", args=[self.ada.slug]))

    def test_the_language_door_follows_a_referer_on_this_host(self):
        response = self.post("set_preferred_language", {"language": "xx"},
                             "http://testserver/vault/")
        self.assertEqual(response["Location"], "http://testserver/vault/")

    def test_the_map_sharing_door_ignores_an_off_site_referer(self):
        response = self.post("set_location_sharing", {"location_sharing": "nonsense"}, EVIL)
        self.assertEqual(response["Location"], reverse("socialhub:profile_list"))

    def test_the_pin_door_ignores_an_off_site_referer(self):
        response = self.post("set_my_address", {"latitude": "x"}, "//evil.example.com/")
        self.assertEqual(response["Location"], reverse("socialhub:profile_list"))
        response = self.post("set_my_address", {"latitude": "91", "longitude": "0"}, EVIL)
        self.assertEqual(response["Location"], reverse("socialhub:profile_list"))
