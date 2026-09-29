"""A person's own settings doors and the socialhub's JSON twins.

The language and map-sharing doors change one thing each and never fail the
person; the API answers what the pages answer — no clearance for a member.

    DJANGO_SETTINGS_MODULE=zenobia.settings manage.py test toto.socialhub.tests_more_profile_api
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.people.models import LocationSharing, Person
from toto.socialhub.models import Clearance, Community, CommunityNewsPost

User = get_user_model()


class ProfileCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.user = User.objects.create_user("ada", "ada@example.com", "pw")
        cls.ada = Person.objects.create(user=cls.user, display_name="Ada")

    def setUp(self):
        self.addCleanup(translation.activate, settings.LANGUAGE_CODE)
        self.client.force_login(self.user)

    def messages_of(self, response):
        return [str(m) for m in get_messages(response.wsgi_request)]


class LanguageTests(ProfileCase):
    def test_a_known_language_is_saved_on_the_profile_and_the_session(self):
        response = self.client.post(reverse("socialhub:set_preferred_language"),
                                    {"language": " pl "})
        self.assertRedirects(response, reverse("socialhub:profile_details", args=[self.ada.slug]),
                             fetch_redirect_response=False)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.preferred_language, "pl")
        self.assertEqual(self.client.session["_language"], "pl")

    def test_an_unknown_language_is_refused_and_nothing_changes(self):
        before = self.ada.preferred_language
        response = self.client.post(reverse("socialhub:set_preferred_language"),
                                    {"language": "xx"}, HTTP_REFERER="/somewhere/")
        self.assertRedirects(response, "/somewhere/", fetch_redirect_response=False)
        self.assertIn("Invalid language selected.", self.messages_of(response))
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.preferred_language, before)
        self.assertNotIn("_language", self.client.session)

    def test_a_login_without_a_person_is_told_and_sent_home(self):
        self.client.force_login(User.objects.create_user("stray", password="pw"))
        response = self.client.post(reverse("socialhub:set_preferred_language"),
                                    {"language": "en"})
        self.assertRedirects(response, "/", fetch_redirect_response=False)
        self.assertIn("Could not save language preference.", self.messages_of(response))


class LocationSharingTests(ProfileCase):
    def share(self, choice, **extra):
        return self.client.post(reverse("socialhub:set_location_sharing"),
                                {"location_sharing": choice}, **extra)

    def test_a_get_changes_nothing(self):
        response = self.client.get(reverse("socialhub:set_location_sharing"),
                                   {"location_sharing": LocationSharing.APPROXIMATE})
        self.assertRedirects(response, reverse("socialhub:profile_list"),
                             fetch_redirect_response=False)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.location_sharing, LocationSharing.OFF)

    def test_sharing_without_an_address_is_saved_and_says_it_shows_nothing_yet(self):
        response = self.share(LocationSharing.APPROXIMATE)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.location_sharing, LocationSharing.APPROXIMATE)
        self.assertTrue(any("no address on your profile yet" in m
                            for m in self.messages_of(response)))

    def test_off_is_accepted_and_a_person_less_login_is_refused(self):
        self.share(LocationSharing.APPROXIMATE)
        self.share(LocationSharing.OFF)
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.location_sharing, LocationSharing.OFF)
        self.client.force_login(User.objects.create_user("stray", password="pw"))
        response = self.share(LocationSharing.OFF)
        self.assertIn("You have no profile to share.", self.messages_of(response))


class ApiCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.guild = Community.objects.create(name="Guild", slug="guild")
        cls.internal = Clearance.objects.create(name="internal", slug="internal")
        cls.elder_user = User.objects.create_user("elder", "elder@example.com", "pw")
        cls.elder = Person.objects.create(user=cls.elder_user, display_name="Elder",
                                          email="elder@example.com", phone="+48 600")
        cls.junior_user = User.objects.create_user("junior", "junior@example.com", "pw")
        cls.junior = Person.objects.create(user=cls.junior_user, display_name="Junior",
                                           patron=cls.elder)
        cls.guild.members.add(cls.elder, cls.junior)
        cls.internal.members.add(cls.elder)
        cls.guild.senior_members.add(cls.elder)
        cls.root = User.objects.create_superuser("root", "root@example.com", "pw")

    def get(self, user, url):
        self.client.force_login(add_to_mesh(user))
        return self.client.get(url)


class CommunityApiTests(ApiCase):
    def test_the_detail_lists_seniors_and_the_latest_news(self):
        CommunityNewsPost.objects.create(community=self.guild, title="Old", content="<p>x</p>")
        CommunityNewsPost.objects.create(community=self.guild, title="New", content="<p>y</p>")
        body = self.get(self.junior_user, "/socialhub/api/communities/guild/").json()
        self.assertEqual(body["members"], [{"id": self.elder.pk, "slug": self.elder.slug,
                                            "name": "Elder"}])
        self.assertEqual(body["member_count"], 1)
        self.assertEqual(body["latest_news_title"], "New")

    def test_the_org_chart_follows_patrons_and_carries_contacts(self):
        nodes = self.get(self.junior_user,
                         "/socialhub/api/communities/guild/org-chart/").json()["nodes"]
        by_slug = {node["slug"]: node for node in nodes}
        self.assertEqual(by_slug[self.junior.slug]["pid"], str(self.elder.pk))
        self.assertIsNone(by_slug[self.elder.slug]["pid"])
        self.assertEqual((by_slug[self.elder.slug]["email"], by_slug[self.elder.slug]["phone"]),
                         ("elder@example.com", "+48 600"))
        self.assertEqual(by_slug[self.junior.slug]["phone"], "")
        self.assertEqual(self.get(self.junior_user,
                                  "/socialhub/api/communities/nowhere/org-chart/").status_code, 404)

    def test_a_profile_counts_communities_and_never_a_clearance(self):
        mine = self.get(self.junior_user, f"/socialhub/api/profiles/{self.elder.slug}/").json()
        root = self.get(self.root, f"/socialhub/api/profiles/{self.elder.slug}/").json()
        self.assertEqual(mine["community_count"], 1)
        self.assertEqual(root["community_count"], 1)
        self.assertEqual([c["slug"] for c in root["communities"]], ["guild"])
        listed = self.get(self.junior_user, "/socialhub/api/profiles/").json()["profiles"]
        self.assertEqual({p["slug"]: p["community_count"] for p in listed}[self.elder.slug], 1)

    def test_outside_the_mesh_a_read_is_gated(self):
        self.client.force_login(self.junior_user)
        response = self.client.get("/socialhub/api/communities/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()["gated"])
