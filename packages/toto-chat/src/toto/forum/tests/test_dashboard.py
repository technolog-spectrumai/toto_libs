"""The dashboard's Forum section (stage 68, 2026-10-07): one entry for each
community whose channel the member may open, built from the access rule the
doors ask.

    manage.py test toto.forum.tests.test_dashboard
"""

import re

from django.db import connection
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from toto.core.dashboard import DashboardSection, sections_for
from toto.forum import access
from toto.forum.models import ForumChannel
from toto.forum.plugins import dashboard_sections
from toto.forum.testing import ForumCase, client_of, member, on_plan, upload
from toto.socialhub.models import Community

SECTION = re.compile(r'<section[^>]*data-dashboard-section="forum".*?</section>', re.S)


class SectionCase(ForumCase):
    def dashboard(self, user):
        return client_of(user).get(reverse("core:dashboard")).content.decode()

    def section(self, user):
        """The Forum section's markup on ``user``'s dashboard, or None."""
        found = SECTION.search(self.dashboard(user))
        return found.group(0) if found else None

    def channel_url(self, community):
        return reverse("forum:channel_detail", args=[community.slug])


class WhoSeesWhatTests(SectionCase):
    def test_a_member_sees_their_communities_and_only_theirs(self):
        for user in (self.member, self.senior, self.head):
            section = self.section(user)
            self.assertIsNotNone(section, user.username)
            self.assertIn(f'href="{self.channel_url(self.guild)}"', section)
            self.assertNotIn(self.channel_url(self.other), section)
        section = self.section(self.outsider)
        self.assertIn(f'href="{self.channel_url(self.other)}"', section)
        self.assertNotIn(self.channel_url(self.guild), section)

    def test_an_administrator_sees_every_community(self):
        section = self.section(self.admin)
        self.assertIn(f'href="{self.channel_url(self.guild)}"', section)
        self.assertIn(f'href="{self.channel_url(self.other)}"', section)

    def test_no_section_on_free(self):
        self.assertIsNone(self.section(self.free))
        self.assertNotIn("/forum/", self.dashboard(self.free))

    def test_no_section_for_a_signed_out_visitor(self):
        from django.test import Client

        page = Client().get(reverse("core:dashboard"), follow=True).content.decode()
        self.assertIsNone(SECTION.search(page))
        self.assertNotIn(self.channel_url(self.guild), page)
        request = RequestFactory().get("/")
        from django.contrib.auth.models import AnonymousUser

        request.user = AnonymousUser()
        self.assertEqual([s for s in sections_for(request) if s["key"] == "forum"], [])

    def test_staff_alone_gets_no_more_than_a_member(self):
        # Staff who belongs to no community: the sentence, and no community.
        section = self.section(self.staff)
        self.assertIsNotNone(section)
        self.assertNotIn(self.channel_url(self.guild), section)
        self.assertNotIn(self.channel_url(self.other), section)
        self.assertIn("data-dashboard-note", section)
        # Staff who belongs to one: that one only.
        clerk, person = member("clerk", is_staff=True)
        on_plan(clerk)
        person.communities.add(self.other)
        section = self.section(clerk)
        self.assertIn(self.channel_url(self.other), section)
        self.assertNotIn(self.channel_url(self.guild), section)

    def test_a_member_of_no_community_is_told_where_a_forum_comes_from(self):
        loner, _person = member("loner")
        on_plan(loner)
        section = self.section(loner)
        self.assertIn("A forum belongs to a community", section)
        self.assertIn(f'href="{reverse("socialhub:community_list")}"', section)

    def test_the_list_is_the_access_rules_own(self):
        for user in (self.member, self.head, self.senior, self.admin, self.outsider,
                     self.staff, self.free):
            self.assertEqual(
                [entry["community"].pk for entry in dashboard_sections.entries_for(user)],
                [community.pk for community in access.communities_of(user)], user.username)

    def test_leaving_a_community_takes_its_entry(self):
        self.member_person.communities.remove(self.guild)
        self.assertNotIn(self.channel_url(self.guild), self.section(self.member))


class EntryTests(SectionCase):
    def test_a_community_without_a_channel_yet_is_listed(self):
        self.assertFalse(ForumChannel.objects.filter(community=self.other).exists())
        section = self.section(self.outsider)
        self.assertIn(self.channel_url(self.other), section)
        self.assertIn("no messages yet", section)
        # Listing it made nothing: the channel is made on its first opening.
        self.assertFalse(ForumChannel.objects.filter(community=self.other).exists())

    def test_the_count_and_the_last_post_and_never_the_text(self):
        self.say(self.member, "the secret plan for Friday")
        self.say(self.second, "a second word", image=upload())
        gone = self.say(self.member, "regret").json()["message"]["id"]
        from toto.forum.testing import send_json

        send_json(client_of(self.member), self.url("message_remove", gone))
        (entry,) = dashboard_sections.entries_for(self.head)
        self.assertEqual(entry["messages"], 2)
        self.assertIsNotNone(entry["last"])
        section = self.section(self.head)
        self.assertIn("2 messages, the last on", section)
        self.assertIn("Guild", section)
        for text in ("the secret plan for Friday", "a second word", "regret"):
            self.assertNotIn(text, self.dashboard(self.head))

    def test_a_communitys_name_is_text(self):
        Community.objects.filter(pk=self.guild.pk).update(
            name='<b>Guild</b> <script>alert(1)</script>')
        section = self.section(self.member)
        self.assertNotIn("<script>alert(1)</script>", section)
        self.assertNotIn("<b>Guild</b>", section)
        self.assertIn("&lt;b&gt;Guild&lt;/b&gt;", section)

    def test_the_entries_are_not_counted_among_the_modules(self):
        def modules(page):
            return int(re.search(r"(\d+) modules", page).group(1))

        before = modules(self.dashboard(self.admin))
        for n in range(5):
            Community.objects.create(name=f"More {n}")
        self.assertEqual(modules(self.dashboard(self.admin)), before)

    def test_the_tile_in_basic_stays(self):
        page = self.dashboard(self.member)
        self.assertIn(f'href="{reverse("forum:channel_list")}"', page)


class CostTests(SectionCase):
    def queries(self, user):
        request = RequestFactory().get("/")
        request.user = user
        plugin = DashboardSection.get("forum")
        with CaptureQueriesContext(connection) as captured:
            made = plugin.section(request)
        return len(captured), made

    def test_the_query_count_does_not_grow_with_the_communities(self):
        self.say(self.member, "one")
        few, made = self.queries(self.admin)
        self.assertEqual(len(made["items"]), 2)
        for n in range(12):
            community = Community.objects.create(name=f"More {n}")
            self.member_person.communities.add(community)
        many, made = self.queries(self.admin)
        self.assertEqual(len(made["items"]), 14)
        self.assertEqual(many, few)
        few_member, _made = self.queries(self.outsider)
        many_member, made = self.queries(self.member)
        self.assertEqual(len(made["items"]), 13)
        self.assertEqual(many_member, few_member)

    def test_the_list_itself_is_two_queries(self):
        """The communities, and one query of counts and dates for them all."""
        self.say(self.member, "one")
        for n in range(6):
            Community.objects.create(name=f"More {n}")
        communities = list(access.communities_of(self.admin))
        from unittest import mock

        with mock.patch.object(access, "communities_of", return_value=communities):
            with self.assertNumQueries(1):
                entries = dashboard_sections.entries_for(self.admin)
        self.assertEqual(len(entries), 8)
