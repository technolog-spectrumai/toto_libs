"""A platform that keeps no positions (2026-10-11):
``settings.SOCIALHUB_ORG_POSITIONS = False``. No Organisation chart tab, no
position doors, and the Org Chart button draws the members by who vouched
for whom, as before positions existed, whatever rows the table holds.

    manage.py test toto.socialhub.tests_org_chart_off
"""
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person

from . import org_chart
from .models import Community, CommunityPosition

User = get_user_model()


class PositionsOffTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.root = User.objects.create_superuser("root", password="pw")
        cls.head = Person.objects.create(user=cls.root, display_name="Root")
        cls.guild = Community.objects.create(name="Cedar Guild", slug="cedar", head=cls.head)
        cls.member = Person.objects.create(
            user=User.objects.create_user("mia", password="pw"), display_name="Mia",
            patron=cls.head)
        cls.head.communities.add(cls.guild)
        cls.member.communities.add(cls.guild)
        cls.position = CommunityPosition.objects.create(community=cls.guild, title="Lead",
                                                        person=cls.member)

    def drawing(self):
        self.client.force_login(self.root)
        return self.client.get(reverse("socialhub:community_org_chart_data_by_slug",
                                       args=[self.guild.slug])).json()

    def test_positions_are_kept_unless_the_host_says_otherwise(self):
        with override_settings():
            from django.conf import settings

            if hasattr(settings, "SOCIALHUB_ORG_POSITIONS"):
                del settings.SOCIALHUB_ORG_POSITIONS
            self.assertTrue(org_chart.positions_enabled())
            self.assertEqual(self.drawing()["kind"], "positions")

    @override_settings(SOCIALHUB_ORG_POSITIONS=False)
    def test_without_positions_the_chart_is_the_patrons(self):
        self.assertFalse(org_chart.positions_enabled())
        drawn = self.drawing()
        self.assertEqual(drawn["kind"], "patrons")
        by_name = {node["name"]: node for node in drawn["nodes"]}
        self.assertEqual(by_name["Mia"]["pid"], str(self.head.pk))
        # No tab, even for who could change the chart, and no forms.
        page = self.client.get(reverse("socialhub:community_detail", args=[self.guild.slug]))
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("chart", [tab["key"] for tab in page.context["community_tabs"]])
        self.assertNotContains(page, reverse("socialhub:position_create", args=[self.guild.slug]))

    @override_settings(SOCIALHUB_ORG_POSITIONS=False)
    def test_the_position_doors_are_closed(self):
        self.client.force_login(self.root)
        made = self.client.post(reverse("socialhub:position_create", args=[self.guild.slug]),
                                {"title": "Second"})
        self.assertEqual(made.status_code, 404)
        gone = self.client.post(reverse("socialhub:position_delete",
                                        args=[self.guild.slug, self.position.pk]))
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(CommunityPosition.objects.count(), 1)       # the row stays, unread
