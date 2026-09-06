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
        U = get_user_model()
        cls.user = U.objects.create_user(username="viewer", password="pw")
        cls.person = Person.objects.create(user=cls.user, display_name="Viewer")
        cls.company = Company.objects.create(name="Acme", slug="acme")
        cls.event = ScheduledEvent.objects.create(
            title="AGM", owner=cls.person, public=True,
            start_time=timezone.now() + timedelta(days=3),
            end_time=timezone.now() + timedelta(days=3, hours=2))
        CompanyEvent.objects.create(company=cls.company, event=cls.event)

        # Linked to the company, but private and owned by SOMEBODY ELSE.
        cls.other = Person.objects.create(
            user=U.objects.create_user(username="other", password="pw"),
            display_name="Other")
        cls.private = ScheduledEvent.objects.create(
            title="BoardOnly", owner=cls.other, public=False,
            start_time=timezone.now() + timedelta(days=4),
            end_time=timezone.now() + timedelta(days=4, hours=1))
        CompanyEvent.objects.create(company=cls.company, event=cls.private)

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

    def test_a_linked_private_event_is_NOT_shown_to_someone_outside_it(self):
        """The leak this tab could have been. The join says the event belongs
        to this company; `visible_events` says whether THIS viewer may see it.
        Both are needed — with only the join, opening a company page would
        list every private meeting anybody linked to it.

        The docstring at the top of this file claimed this coverage before the
        test existed, which is worse than no test: a change dropping
        `visible_events` would have passed a suite that said it was checked.
        """
        self.client.force_login(self.user)
        r = self.client.get(reverse("company:events", args=["acme"]))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "AGM")
        self.assertNotContains(r, "BoardOnly")

    def test_its_owner_does_see_it(self):
        """The other half: the filter must not hide an event from its owner."""
        self.client.force_login(self.other.user)
        r = self.client.get(reverse("company:events", args=["acme"]))
        self.assertContains(r, "BoardOnly")
