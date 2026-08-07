"""One budget per mission — and none per task.

The "not per task" half is a real requirement, not a phrasing: a task is a unit
of work and a board where every card carries a number is a board whose numbers
are wrong by Thursday. It is asserted here because nothing else would notice
somebody helpfully adding the field to Task later.

The currency is a symbol rather than an FK to ``assets.Asset``, and that is also
tested, because the reason is easy to forget: toto-works depends on toto-base
alone, ``assets`` ships in toto-economy, and studio and aurelian install kanban
without it. An FK would hand those two hosts a migration they cannot build.
"""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from toto.api.testutils import add_to_mesh
from toto.core.models import Platform
from toto.kanban.models import (
    Campaign, Mission, Practitioner, Project, ProjectCommitment, Task,
)
from toto.people.models import Person

User = get_user_model()


def _person(username, **kwargs):
    user = User.objects.create_user(username=username, password="pass", **kwargs)
    person = Person.objects.create(
        user=user, display_name=username.title(), email=f"{username}@x.com")
    return user, person


class BudgetWorld(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.lead_user, self.lead = _person("lead")
        self.member_user, self.member = _person("member")
        self.stranger_user, self.stranger = _person("stranger")

        self.project = Project.objects.create(name="P", project_lead=self.lead)
        seat = Practitioner.objects.create(person=self.member, role="contributor")
        ProjectCommitment.objects.create(
            practitioner=seat, project=self.project, hours_per_day=4)

        self.campaign = Campaign.objects.create(project=self.project, name="C")
        self.mission = Mission.objects.create(campaign=self.campaign, title="M")

    def url(self):
        return reverse("kanban:mission_budget", args=[self.mission.pk])


class BudgetFieldTests(BudgetWorld):
    def test_a_mission_starts_unbudgeted(self):
        self.assertIsNone(self.mission.budget_amount)
        self.assertFalse(self.mission.has_budget)

    def test_none_is_not_zero(self):
        """"Not budgeted" and "budgeted at nothing" are different answers."""
        self.mission.budget_amount = Decimal("0.00")
        self.mission.budget_currency = "ASR"
        self.mission.full_clean()
        self.mission.save()
        self.assertTrue(self.mission.has_budget)

    def test_an_amount_needs_a_currency(self):
        self.mission.budget_amount = Decimal("100")
        with self.assertRaises(ValidationError) as caught:
            self.mission.full_clean()
        self.assertIn("budget_currency", caught.exception.error_dict)

    def test_a_currency_needs_an_amount(self):
        self.mission.budget_currency = "EUR"
        with self.assertRaises(ValidationError) as caught:
            self.mission.full_clean()
        self.assertIn("budget_currency", caught.exception.error_dict)

    def test_a_negative_budget_is_refused(self):
        self.mission.budget_amount = Decimal("-1")
        self.mission.budget_currency = "EUR"
        with self.assertRaises(ValidationError) as caught:
            self.mission.full_clean()
        self.assertIn("budget_amount", caught.exception.error_dict)

    def test_the_currency_is_normalised(self):
        """Otherwise "eur" and "EUR" both land and look like two currencies."""
        self.mission.budget_amount = Decimal("10")
        self.mission.budget_currency = " eur "
        self.mission.full_clean()
        self.assertEqual(self.mission.budget_currency, "EUR")

    def test_a_task_has_no_budget_field(self):
        """The requirement, asserted: per mission, NOT per task."""
        names = {f.name for f in Task._meta.get_fields()}
        self.assertNotIn("budget_amount", names)
        self.assertNotIn("budget_currency", names)

    def test_the_currency_is_not_a_foreign_key(self):
        """An FK to assets.Asset would be unbuildable on studio and aurelian.

        toto-works depends on toto-base alone; `assets` ships in toto-economy,
        which neither host pins. A CharField costs them nothing.
        """
        field = Mission._meta.get_field("budget_currency")
        self.assertFalse(field.is_relation)


class BudgetViewTests(BudgetWorld):
    def test_a_member_can_set_a_budget(self):
        self.client.force_login(self.member_user)
        response = self.client.post(
            self.url(), {"budget_amount": "1500.50", "budget_currency": "asr"})
        self.assertEqual(response.status_code, 302)
        self.mission.refresh_from_db()
        self.assertEqual(self.mission.budget_amount, Decimal("1500.50"))
        self.assertEqual(self.mission.budget_currency, "ASR")

    def test_clearing_both_fields_removes_the_budget(self):
        self.mission.budget_amount = Decimal("10")
        self.mission.budget_currency = "EUR"
        self.mission.save()

        self.client.force_login(self.member_user)
        self.client.post(self.url(), {"budget_amount": "", "budget_currency": ""})
        self.mission.refresh_from_db()
        self.assertIsNone(self.mission.budget_amount)
        self.assertFalse(self.mission.has_budget)

    def test_an_amount_without_a_currency_is_rejected(self):
        self.client.force_login(self.member_user)
        self.client.post(self.url(), {"budget_amount": "5", "budget_currency": ""})
        self.mission.refresh_from_db()
        self.assertIsNone(self.mission.budget_amount)

    def test_a_stranger_cannot_set_a_budget(self):
        self.client.force_login(self.stranger_user)
        response = self.client.post(
            self.url(), {"budget_amount": "999", "budget_currency": "EUR"})
        self.assertIn(response.status_code, (403, 404))
        self.mission.refresh_from_db()
        self.assertIsNone(self.mission.budget_amount)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(self.url()).status_code, 405)

    def test_the_mission_page_shows_the_budget(self):
        self.mission.budget_amount = Decimal("2500")
        self.mission.budget_currency = "PLN"
        self.mission.save()

        add_to_mesh(self.member_user)
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("kanban:mission_detail", args=[self.mission.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "2500.00")
        self.assertContains(response, "PLN")

    def test_the_mission_page_says_so_when_there_is_no_budget(self):
        add_to_mesh(self.member_user)
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("kanban:mission_detail", args=[self.mission.pk]))
        self.assertContains(response, "No budget set")


class BudgetApiTests(BudgetWorld):
    def test_the_mission_api_carries_the_budget_as_a_string(self):
        """Money through a JSON float is money with a rounding bug in it."""
        self.mission.budget_amount = Decimal("1234.56")
        self.mission.budget_currency = "ASR"
        self.mission.save()

        add_to_mesh(self.member_user)
        self.client.force_login(self.member_user)
        response = self.client.get(
            reverse("kanban:api_mission_detail", args=[self.mission.pk]))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["budget_amount"], "1234.56")
        self.assertEqual(payload["budget_currency"], "ASR")

    def test_an_unbudgeted_mission_reports_nulls(self):
        add_to_mesh(self.member_user)
        self.client.force_login(self.member_user)
        payload = self.client.get(
            reverse("kanban:api_mission_detail", args=[self.mission.pk])).json()
        self.assertIsNone(payload["budget_amount"])
        self.assertIsNone(payload["budget_currency"])
