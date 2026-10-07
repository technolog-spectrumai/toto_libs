"""The community page's forum panel, and the news panel it replaced.

The panel is the way into the community's one channel (``toto.forum``,
2026-10-07): there is no row linking a community to a room any more
(``CommunityForum`` is gone; the channel itself holds the one-to-one), and
the panel is drawn only for who may read the channel. Who that is, is
tested with the forum (``toto.forum.tests.test_pages``); here is what this
app alone can say: the link row is gone, nobody who may not read sees a
panel, a host without the forum shows none, and the news POSTS survive —
the change that brought the panel removed a panel, not a model.
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.socialhub.models import Community


class NoLinkRowTests(TestCase):
    def test_the_slug_link_model_is_gone(self):
        import toto.socialhub.models as models

        self.assertFalse(hasattr(models, "CommunityForum"))
        self.assertFalse([m for m in django_apps.get_app_config("socialhub").get_models()
                          if m.__name__ == "CommunityForum"])

    def test_a_visitor_who_may_not_read_the_channel_sees_no_panel(self):
        Platform.objects.create(site_name="T", author="A", publication_year=2026, active=True)
        user = get_user_model().objects.create_user("v", password="pw")
        Community.objects.create(name="Plain", slug="plain")
        self.client.force_login(user)
        response = self.client.get(reverse("socialhub:community_detail", args=["plain"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="community-forum"')


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

    def test_no_forum_panel_on_a_host_without_the_forum(self):
        from unittest.mock import patch

        real = django_apps.is_installed
        self.client.force_login(self.user)
        with patch.object(django_apps, "is_installed",
                          side_effect=lambda name: name != "toto.forum" and real(name)):
            response = self.client.get(
                reverse("socialhub:community_detail", args=["newsy"]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'data-testid="community-forum"')
