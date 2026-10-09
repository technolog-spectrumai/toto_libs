"""No forum panel on the community page, and the news panel it had replaced.

The forum is parked since 2026-10-09 and the hooks other apps carried for it
went with it (toto-chat's ``toto/forum/PARKED.md``). The panel that led into
a community's channel was one of them: its plugin is out of the registry and
its template is out of the tree, so no community page offers a forum,
whatever a host installs and whoever looks. The row that once linked a
community to a room (``CommunityForum``) stays gone, and the news POSTS
survive — the change that brought the forum panel removed the news panel,
not the model, and parking the forum brings neither panel back.

    manage.py test toto.socialhub.tests_forum_room
"""

from __future__ import annotations

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.template import TemplateDoesNotExist
from django.template.loader import get_template
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform
from toto.people.models import Person
from toto.socialhub.models import Community

PANEL = 'data-testid="community-forum"'


def _person(user, name):
    """The user's person: a host may make one with the account."""
    person = Person.objects.filter(user=user).first()
    return person or Person.objects.create(user=user, display_name=name)


class NoLinkRowTests(TestCase):
    def test_the_slug_link_model_is_gone(self):
        import toto.socialhub.models as models

        self.assertFalse(hasattr(models, "CommunityForum"))
        self.assertFalse([m for m in django_apps.get_app_config("socialhub").get_models()
                          if m.__name__ == "CommunityForum"])


class NoForumPanelTests(TestCase):
    """The plugin, its template and the panel itself."""

    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        users = get_user_model().objects
        cls.visitor = users.create_user("v", password="pw")
        cls.member = users.create_user("m", password="pw")
        cls.head = users.create_user("h", password="pw")
        cls.root = users.create_superuser("r", "r@example.org", "pw")
        cls.community = Community.objects.create(name="Plain", slug="plain")
        _person(cls.member, "Member").communities.add(cls.community)
        cls.community.head = _person(cls.head, "Head")
        cls.community.save()

    def test_the_plugin_is_not_registered(self):
        from toto.socialhub.plugins import community_plugins

        self.assertNotIn("community_forum", set(community_plugins.CommunityPlugin.registry))
        self.assertFalse(hasattr(community_plugins, "CommunityForumPlugin"))

    def test_its_template_is_gone(self):
        with self.assertRaises(TemplateDoesNotExist):
            get_template("socialhub/community_plugins/forum.html")

    def test_nobody_sees_a_panel(self):
        """Not a visitor, and not those the panel was drawn for: a member,
        the community's head, an administrator."""
        for user in (self.visitor, self.member, self.head, self.root):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(
                    reverse("socialhub:community_detail", args=["plain"]))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, PANEL)


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
        self.assertNotContains(response, PANEL)

    def test_but_the_post_still_exists(self):
        from toto.socialhub.models import CommunityNewsPost

        CommunityNewsPost.objects.create(community=self.community,
                                         title="Kept", content="<p>x</p>")
        self.assertTrue(
            CommunityNewsPost.objects.filter(title="Kept").exists())

    def test_neither_panel_is_in_the_registry(self):
        from toto.socialhub.plugins.community_plugins import CommunityPlugin

        keys = set(CommunityPlugin.registry)
        self.assertNotIn("community_news", keys)
        self.assertNotIn("community_forum", keys)
