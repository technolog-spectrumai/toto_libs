"""The audit pages actually render — the coverage whose absence cost a 500.

The chain tests prove the record is sound; nothing proved the PAGES were.
All three views arrived from the truth book without ``PageProcessor``, whose
chrome forgave the missing ``platform``; the oya base on a full host does
not, and /audit/ answered 500 to the first superuser who opened it. These
render every page through the real chrome.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.core.models import Platform

from ..services import record

User = get_user_model()


class AuditPageTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        cls.staff = User.objects.create_user("keeper", password="pw",
                                             is_staff=True)
        cls.record = record("CREATE", app_label="audit", obj=cls.staff,
                            actor_user=cls.staff,
                            description="the fixture row")


class IndexPageTests(AuditPageTestCase):
    def test_the_trail_renders_through_the_chrome(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Audit trail")
        self.assertContains(response, "Test Platform")
        self.assertContains(response, "the fixture row")

    def test_the_filters_filter(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:index"),
                                   {"action": "DELETE"})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "the fixture row")

    def test_anonymous_is_sent_to_log_in(self):
        response = self.client.get(reverse("audit:index"))
        self.assertEqual(response.status_code, 302)

    def test_a_member_is_not_shown_the_trail(self):
        member = User.objects.create_user("member", password="pw")
        self.client.force_login(member)
        response = self.client.get(reverse("audit:index"))
        self.assertEqual(response.status_code, 302)


class DetailPageTests(AuditPageTestCase):
    def test_one_record_renders(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:detail",
                                           args=[self.record.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "the fixture row")


class VerifyPageTests(AuditPageTestCase):
    def test_the_verification_page_renders_a_verdict(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("audit:verify"))
        self.assertEqual(response.status_code, 200)
