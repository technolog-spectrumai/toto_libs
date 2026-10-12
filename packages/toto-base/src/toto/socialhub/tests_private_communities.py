"""Private communities (2026-10-12; ``permissions.may_see_members``). The
owner: "only after you are in yourself you can see who is in the community.
What is public instead is number of members."

With ``SOCIALHUB_PRIVATE_COMMUNITIES`` on: a community's page shows everybody
signed in how many members it has, and its members (its head, an
administrator) who they are; the chart, the API and the chips on a profile
keep the same rule. Off, the default, everything is as it was.

    manage.py test toto.socialhub.tests_private_communities
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.socialhub.permissions import communities_shown_to, may_see_members

User = get_user_model()
PRIVATE = override_settings(SOCIALHUB_PRIVATE_COMMUNITIES=True)


def person(name):
    user = User.objects.create_user(name, f"{name}@example.invalid", "pw")
    return user, Person.objects.create(user=user, display_name=name.title())


class Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.head_user, cls.head = person("headperson")
        cls.ann_user, cls.ann = person("ann")
        cls.bob_user, cls.bob = person("bob")
        cls.out_user, cls.out = person("outsider")
        cls.club = Community.objects.create(name="Club", slug="club", head=cls.head)
        cls.other = Community.objects.create(name="Other", slug="other")
        cls.ann.communities.add(cls.club)
        cls.bob.communities.add(cls.club, cls.other)
        cls.out.communities.add(cls.other)

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("socialhub:community_detail", args=[self.club.slug]))


class RuleTests(Fixture):
    def test_off_everybody_reads_the_members(self):
        for user in (self.out_user, self.ann_user, AnonymousUser()):
            self.assertTrue(may_see_members(user, self.club))
        self.assertEqual(set(communities_shown_to(self.out_user)), {self.club, self.other})

    @PRIVATE
    def test_on_the_members_the_head_and_nobody_else(self):
        self.assertTrue(may_see_members(self.ann_user, self.club))
        self.assertTrue(may_see_members(self.head_user, self.club))        # not a member: its head
        self.assertFalse(may_see_members(self.out_user, self.club))
        self.assertFalse(may_see_members(AnonymousUser(), self.club))
        self.assertFalse(may_see_members(User.objects.create_user("bare", password="pw"), self.club))
        self.assertEqual(set(communities_shown_to(self.out_user)), {self.other})
        self.assertEqual(set(communities_shown_to(self.head_user)), {self.club})
        self.assertEqual(set(communities_shown_to(AnonymousUser())), set())


class PageTests(Fixture):
    @PRIVATE
    def test_an_outsider_sees_the_number_and_no_name(self):
        response = self.page(self.out_user)
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn('data-testid="community-member-count">2<', body)
        self.assertIn('data-testid="community-members-private"', body)
        self.assertNotIn('data-testid="community-members"', body)
        for name in ("Ann", "Bob"):
            self.assertNotIn(reverse("socialhub:profile_details", args=[getattr(self, name.lower()).slug]), body)
        self.assertNotIn("showChart = true", body)

    @PRIVATE
    def test_a_member_and_the_head_see_who_is_in(self):
        for user in (self.ann_user, self.head_user):
            body = self.page(user).content.decode()
            self.assertIn('data-testid="community-members"', body)
            self.assertNotIn('data-testid="community-members-private"', body)
            self.assertIn(reverse("socialhub:profile_details", args=[self.bob.slug]), body)
            self.assertIn('data-testid="community-member-count">2<', body)

    def test_off_the_page_is_as_it_was(self):
        body = self.page(self.out_user).content.decode()
        self.assertIn('data-testid="community-members"', body)
        self.assertIn(reverse("socialhub:profile_details", args=[self.ann.slug]), body)

    @PRIVATE
    def test_the_chart_is_the_members_too(self):
        url = reverse("socialhub:community_org_chart_data_by_slug", args=[self.club.slug])
        self.client.force_login(self.out_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.ann_user)
        answer = self.client.get(url)
        self.assertEqual(answer.status_code, 200)
        self.assertEqual({node["name"] for node in answer.json()["nodes"]}, {"Ann", "Bob"})


class ProfileTests(Fixture):
    def chips(self, viewer, whose):
        self.client.force_login(viewer)
        response = self.client.get(
            reverse("socialhub:profile_details", kwargs={"slug": whose.slug}) + "?tab=communities")
        return {c.name for c in response.context["profile"].listed_communities}

    @PRIVATE
    def test_a_profile_names_the_communities_the_viewer_shares(self):
        self.assertEqual(self.chips(self.out_user, self.bob), {"Other"})
        self.assertEqual(self.chips(self.ann_user, self.bob), {"Club"})
        self.assertEqual(self.chips(self.bob_user, self.bob), {"Club", "Other"})      # their own
        self.assertEqual(self.chips(self.out_user, self.ann), set())

    def test_off_a_profile_names_them_all(self):
        self.assertEqual(self.chips(self.out_user, self.bob), {"Club", "Other"})
