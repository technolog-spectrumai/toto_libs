"""The manual's chapters follow the reader and the platform (2026-10-10).

The team's chapter is the team's; the chapter on getting an account describes
the way in this platform has (an invitation or an application); the numbers
are the host's; and the two languages carry the same chapters.

    manage.py test toto.core.tests_manual_chapters
"""
from __future__ import annotations

import re

from django.apps import apps
from django.contrib.auth import get_user_model
from django.template.loader import get_template
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from toto.core import views
from toto.core.models import Platform

User = get_user_model()
BODIES = ("oya/manual/_body_en.html", "oya/manual/_body_pl.html")


class ManualChapterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        cls.member = User.objects.create_user("reader", password="pw")
        cls.staff = User.objects.create_user("ops", password="pw", is_staff=True)

    def page(self, user, language="en"):
        self.client.force_login(user)
        response = self.client.get(reverse("core:manual"), HTTP_ACCEPT_LANGUAGE=language)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def features(self, user):
        request = RequestFactory().get("/core/manual/")
        request.user = user
        return views._manual_features(request)

    def test_the_teams_chapter_is_the_teams(self):
        for language in ("en", "pl"):
            with self.subTest(language=language):
                self.assertNotIn('id="team"', self.page(self.member, language))
                self.assertNotIn('href="#team"', self.page(self.member, language))
                self.assertIn('id="team"', self.page(self.staff, language))
        self.assertFalse(self.features(self.member)["team"])
        self.assertTrue(self.features(self.staff)["team"])
        self.assertFalse(self.features(self.staff)["superuser"])

    def test_a_chapter_the_host_keeps_for_its_team_is_not_a_members(self):
        if not views._mounted("workflows:workflow_list"):
            self.skipTest("this host mounts no workflow pages")
        with override_settings(MANUAL_TEAM_CHAPTERS=()):
            self.assertTrue(self.features(self.member)["workflows_open"])
            self.assertIn('id="workflows"', self.page(self.member))
        with override_settings(MANUAL_TEAM_CHAPTERS=("workflows",)):
            self.assertFalse(self.features(self.member)["workflows"])
            self.assertTrue(self.features(self.staff)["workflows"])
            self.assertFalse(self.features(self.staff)["workflows_open"])
            body = self.page(self.member)
            self.assertNotIn('id="workflows"', body)
            self.assertNotIn('href="#workflows"', body)
            # The team reads of them in its own chapter.
            self.assertIn(reverse("workflows:workflow_list"), self.page(self.staff))

    def test_the_way_in_described_is_the_platforms(self):
        if not apps.is_installed("toto.socialhub"):
            self.skipTest("this host has no socialhub")
        with override_settings(SOCIALHUB_ENROLLMENT="invitation"):
            body = self.page(self.member)
            self.assertIn("Make my account", body)
            self.assertNotIn("Apply for membership", body)
            self.assertIn("Issue the invitation", self.page(self.staff))
        with override_settings(SOCIALHUB_ENROLLMENT="application"):
            body = self.page(self.member)
            self.assertIn("Apply for membership", body)
            self.assertNotIn("Make my account", body)
            self.assertNotIn("Issue the invitation", self.page(self.staff))

    def test_the_numbers_are_the_hosts(self):
        with override_settings(LOGIN_LOCK_AFTER=4, LOGIN_LOCK_MINUTES=9):
            body = re.sub(r"\s+", " ", self.page(self.member))
            self.assertIn("after 4, signing in", body)
            self.assertIn("paused for 9 minutes", body)
        if apps.is_installed("toto.vault"):
            with override_settings(VAULT_TRASH_DAYS=7):
                self.assertIn("for 7 days", re.sub(r"\s+", " ", self.page(self.member)))
        if apps.is_installed("toto.socialhub"):
            with override_settings(SOCIALHUB_ENROLLMENT="invitation", SOCIALHUB_INVITATION_HOURS=5):
                self.assertIn("for 5 hours", re.sub(r"\s+", " ", self.page(self.member)))

    def test_a_public_file_is_a_visitors_only_where_the_host_lets_visitors_in(self):
        self.assertTrue(views._public_route("no:such-route") in (True, False))
        with override_settings(MIDDLEWARE=["django.middleware.common.CommonMiddleware"]):
            # No gate with a public list: the view decides, so the route is open.
            self.assertTrue(views._public_route("vault:public_file"))

    def test_both_languages_carry_the_same_chapters(self):
        def shape(name):
            source = get_template(name).template.source
            return (re.findall(r'<section id="([^"]+)"', source),
                    re.findall(r'href="#([a-z-]+)"', source),
                    sorted(re.findall(r"\{% (?:if|elif) ([^%]+?) %\}", source)))

        english, polish = shape(BODIES[0]), shape(BODIES[1])
        self.assertEqual(english[0], polish[0])          # the chapters, in order
        self.assertEqual(english[1], polish[1])          # the contents
        self.assertEqual(english[2], polish[2])          # every condition
        self.assertEqual(sorted(set(english[1])), sorted(set(english[0])))
