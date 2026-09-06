"""One forum per company, linked from the company view."""
import unittest

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class CompanyForumLinkTests(TestCase):
    """Needs the forum app: it makes a real room to link to.

    Skipped rather than failed on a host without chat — `NoForumInstalledTests`
    below covers that half, and importing `toto.forum.models` when the app is
    not installed raises at IMPORT time (RuntimeError: doesn't declare an
    explicit app_label), which no `try` inside a test method can catch.
    """

    @classmethod
    def setUpClass(cls):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.forum"):
            raise unittest.SkipTest("toto.forum is not installed on this host")
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        from toto.company.models import Company, CompanyForum
        from toto.core.models import Platform
        from toto.forum.models import ForumChannel

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        cls.user = get_user_model().objects.create_user(username="v", password="pw")
        cls.company = Company.objects.create(name="Acme", slug="acme")
        cls.plain = Company.objects.create(name="Plain", slug="plain")
        cls.room = ForumChannel.objects.create(name="Acme room", slug="acme-room")
        CompanyForum.objects.create(company=cls.company, channel_slug="acme-room")

    def test_the_link_is_on_the_company_page(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:structure", args=["acme"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Company forum")
        self.assertContains(r, reverse("forum:channel_detail", args=["acme-room"]))

    def test_it_shows_on_every_tab_because_it_is_in_the_header(self):
        self.client.force_login(self.user)
        for tab in ("shareholders", "locations", "events"):
            with self.subTest(tab=tab):
                r = self.client.get(reverse(f"company:{tab}", args=["acme"]))
                self.assertContains(r, "Company forum")

    def test_a_company_with_no_room_shows_no_link(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:structure", args=["plain"]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Company forum")

    def test_a_dangling_slug_renders_the_page_without_a_link(self):
        """The cost of a slug instead of an FK, pinned: deleting the room
        leaves the row pointing at nothing. That must be a MISSING link, not
        a 500 and not a dead button."""
        from toto.company.models import CompanyForum

        CompanyForum.objects.filter(company=self.company).update(
            channel_slug="gone-room")
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:structure", args=["acme"]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Company forum")

    def test_one_forum_per_company(self):
        """The OneToOne, asserted rather than assumed."""
        from django.db.utils import IntegrityError

        from toto.company.models import CompanyForum

        with self.assertRaises(IntegrityError):
            CompanyForum.objects.create(
                company=self.company, channel_slug="second-room")


class NoForumInstalledTests(TestCase):
    """The company page must render on a host with no chat at all.

    This is the case the slug design exists for: an FK to
    `forum.ForumChannel` would have made toto-business depend on toto-chat,
    and `check_package_graph.py` refuses that outright. A slug costs a
    dangling-row possibility (covered above) and buys a company register that
    works without a forum.

    `BUILD_CHAT=0` really does drop the app on this host — verified, not
    assumed — so `is_installed` is False and `company.forum.channel()` must
    answer None rather than raising.
    """

    @classmethod
    def setUpTestData(cls):
        from toto.company.models import Company, CompanyForum
        from toto.core.models import Platform

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        cls.user = get_user_model().objects.create_user(username="n", password="pw")
        cls.company = Company.objects.create(name="NoChat", slug="nochat")
        CompanyForum.objects.create(company=cls.company, channel_slug="whatever")

    def test_channel_is_none_when_the_forum_app_is_absent(self):
        from django.apps import apps as django_apps

        if django_apps.is_installed("toto.forum"):
            self.skipTest("this run has the forum installed; see the gate's "
                          "BUILD_CHAT=0 stanza for the other half")
        self.assertIsNone(self.company.forum.channel())

    def test_the_page_still_renders(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:structure", args=["nochat"]))
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Company forum")


class ForumLinkEditingTests(TestCase):
    """Staff set the room from the company page; members may not."""

    @classmethod
    def setUpClass(cls):
        from django.apps import apps as django_apps

        if not django_apps.is_installed("toto.forum"):
            raise unittest.SkipTest("toto.forum is not installed on this host")
        super().setUpClass()

    @classmethod
    def setUpTestData(cls):
        from toto.company.models import Company
        from toto.core.models import Platform
        from toto.forum.models import ForumChannel

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        U = get_user_model()
        cls.staff = U.objects.create_user(username="st", password="pw", is_staff=True)
        cls.member = U.objects.create_user(username="me", password="pw")
        cls.company = Company.objects.create(name="Edit Co", slug="editco")
        ForumChannel.objects.create(name="Edit room", slug="edit-room")

    def url(self):
        return reverse("company:structure", args=["editco"])

    def test_staff_can_set_the_room(self):
        from toto.company.models import CompanyForum

        self.client.force_login(self.staff)
        r = self.client.post(self.url(),
                             {"action": "forum", "channel_slug": "edit-room"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(
            CompanyForum.objects.get(company=self.company).channel_slug,
            "edit-room")

    def test_an_empty_slug_removes_the_link_rather_than_storing_nothing(self):
        """`CompanyForum` is a OneToOne: "no room" is the absence of the row,
        not a row holding an empty string that would render a dead card."""
        from toto.company.models import CompanyForum

        CompanyForum.objects.create(company=self.company, channel_slug="edit-room")
        self.client.force_login(self.staff)
        self.client.post(self.url(), {"action": "forum", "channel_slug": ""})
        self.assertFalse(CompanyForum.objects.filter(company=self.company).exists())

    def test_a_member_may_not_edit_it(self):
        self.client.force_login(self.member)
        r = self.client.post(self.url(),
                             {"action": "forum", "channel_slug": "edit-room"})
        self.assertEqual(r.status_code, 403)

    def test_the_edit_button_is_staff_only(self):
        self.client.force_login(self.member)
        body = self.client.get(self.url()).content.decode()
        self.assertIn("Forum", body)
        self.assertNotIn("modal = 'forum'", body)

    def test_a_slug_naming_no_room_is_shown_rather_than_hidden(self):
        """The cost of a slug instead of an FK, surfaced so somebody can fix
        it — a silently missing card would look like 'no room linked'."""
        from toto.company.models import CompanyForum

        CompanyForum.objects.create(company=self.company, channel_slug="gone")
        self.client.force_login(self.staff)
        self.assertContains(self.client.get(self.url()), "gone")
