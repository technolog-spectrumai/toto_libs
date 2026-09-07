"""The community's room, and the news panel it replaced.

The interesting assertions are the two that are easy to leave out. The room can
be MISSING while the link row exists — the slug resolves at render time and
nothing cascades — and that has to render as "no room yet" rather than as a
link nobody can open. And the news POSTS must survive: this change removed a
panel, not a model, so a community that had written news still has it.
"""

from __future__ import annotations

import unittest

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.socialhub.models import Community, CommunityForum


class CommunityForumPanelTests(TestCase):
    """Needs the forum app: it makes a real room to link to."""

    @classmethod
    def setUpClass(cls):
        if not django_apps.is_installed("toto.forum"):
            raise unittest.SkipTest("toto.forum is not installed on this host")
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        from toto.forum.models import ForumChannel

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        cls.user = get_user_model().objects.create_user("v", password="pw")
        cls.linked = Community.objects.create(name="Linked", slug="linked")
        cls.plain = Community.objects.create(name="Plain", slug="plain")
        cls.stale = Community.objects.create(name="Stale", slug="stale")
        cls.room = ForumChannel.objects.create(name="Guild hall",
                                               slug="guild-hall")
        CommunityForum.objects.create(community=cls.linked,
                                      channel_slug="guild-hall")
        # A row naming a room that does not exist — the state a deleted room
        # leaves behind, since the slug is not a foreign key.
        CommunityForum.objects.create(community=cls.stale,
                                      channel_slug="was-deleted")

    def page(self, slug):
        self.client.force_login(self.user)
        return self.client.get(reverse("socialhub:community_detail",
                                       args=[slug]))

    def test_the_room_is_linked_from_the_community_page(self):
        response = self.page("linked")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Guild hall")
        self.assertContains(response, reverse("forum:channel_detail",
                                              args=["guild-hall"]))

    def test_a_community_with_no_room_says_so(self):
        response = self.page("plain")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No forum room is linked")

    def test_a_row_pointing_at_a_deleted_room_reads_as_no_room(self):
        """Not as a link to nothing. See the module docstring."""
        response = self.page("stale")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No forum room is linked")
        self.assertNotContains(response, "was-deleted")

    def test_channel_resolves_the_room(self):
        link = CommunityForum.objects.get(community=self.linked)
        self.assertEqual(link.channel().pk, self.room.pk)

    def test_channel_is_none_when_the_room_is_gone(self):
        self.assertIsNone(
            CommunityForum.objects.get(community=self.stale).channel())


class NewsIsGoneFromThePageOnlyTests(TestCase):
    """The panel went; the posts did not."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        cls.user = get_user_model().objects.create_user("w", password="pw")
        cls.community = Community.objects.create(name="Newsy", slug="newsy")

    def test_the_news_panel_is_not_rendered(self):
        from toto.socialhub.models import CommunityNewsPost

        CommunityNewsPost.objects.create(community=self.community,
                                         title="Old announcement",
                                         content="<p>hello</p>")
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("socialhub:community_detail", args=["newsy"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Old announcement")

    def test_but_the_post_still_exists(self):
        from toto.socialhub.models import CommunityNewsPost

        CommunityNewsPost.objects.create(community=self.community,
                                         title="Kept", content="<p>x</p>")
        self.assertTrue(
            CommunityNewsPost.objects.filter(title="Kept").exists())

    def test_the_forum_plugin_replaced_it_in_the_registry(self):
        from toto.socialhub.plugins.community_plugins import CommunityPlugin

        keys = set(CommunityPlugin.registry)
        self.assertIn("community_forum", keys)
        self.assertNotIn("community_news", keys)
