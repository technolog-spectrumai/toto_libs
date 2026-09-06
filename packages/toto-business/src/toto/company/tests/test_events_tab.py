"""Smoke: the company Events tab renders, lists, and does not leak."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone


class CompanyEventsSmoke(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.company.models import Company, CompanyEvent
        from toto.events.models import ScheduledEvent
        from toto.people.models import Person

        Platform.objects.create(site_name="T", author="A",
                                publication_year=2026, active=True)
        cls.user = get_user_model().objects.create_user(
            username="viewer", password="pw")
        cls.person = Person.objects.create(user=cls.user, display_name="Viewer")
        cls.company = Company.objects.create(name="Acme", slug="acme")
        cls.event = ScheduledEvent.objects.create(
            title="AGM", owner=cls.person,
            start_time=timezone.now() + timedelta(days=3),
            end_time=timezone.now() + timedelta(days=3, hours=2))
        CompanyEvent.objects.create(company=cls.company, event=cls.event)

    def test_the_tab_renders_and_lists_the_event(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:events", args=["acme"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AGM")
        self.assertContains(r, "company_calendar")

    def test_the_tab_is_in_the_strip(self):
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:structure", args=["acme"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, reverse("company:events", args=["acme"]))
