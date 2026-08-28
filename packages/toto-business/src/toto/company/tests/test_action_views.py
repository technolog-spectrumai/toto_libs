"""The Actions page and the whole draft-to-block flow."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from toto.company.integration import ledger as bc_ledger
from toto.company.models import ActionStatus, CompanyAction
from toto.company.tests.factories import CompanyFactoryMixin
from toto.core.models import Platform


class ActionViewTestCase(CompanyFactoryMixin, TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test Platform", author="Tests",
                                publication_year=2026, active=True)
        self.staff = get_user_model().objects.create_user("boss", password="x",
                                                          is_staff=True)
        self.member = get_user_model().objects.create_user("clerk", password="x")
        self.company = self.make_company()

    def url(self, name="company:actions", *args):
        return reverse(name, args=[self.company.slug, *args])


class AccessTests(ActionViewTestCase):
    def test_the_page_requires_login(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_plain_member_may_read_but_not_draft(self):
        self.client.force_login(self.member)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        response = self.client.post(self.url(), {"title": "x", "kind": "resolution",
                                                 "body": "y"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(CompanyAction.objects.exists())

    def test_a_plain_member_cannot_record(self):
        action = CompanyAction.objects.create(company=self.company, title="T", body="B")
        self.client.force_login(self.member)
        response = self.client.post(
            self.url("company:action_record", action.uid),
        )
        self.assertEqual(response.status_code, 403)
        action.refresh_from_db()
        self.assertEqual(action.status, ActionStatus.DRAFT)


class DraftTests(ActionViewTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)

    def test_staff_can_write_a_draft(self):
        self.client.post(self.url(), {
            "title": "Adopt the 2027 plan", "kind": "resolution",
            "body": "The board adopts the plan.",
        }, follow=True)
        action = CompanyAction.objects.get()
        self.assertEqual(action.status, ActionStatus.DRAFT)
        self.assertEqual(action.created_by, self.staff)

    def test_a_draft_can_be_deleted(self):
        action = CompanyAction.objects.create(company=self.company, title="T", body="B")
        self.client.post(self.url("company:action_delete", action.uid), follow=True)
        self.assertFalse(CompanyAction.objects.filter(pk=action.pk).exists())

    def test_drafts_and_recorded_actions_are_shown_apart(self):
        draft = CompanyAction.objects.create(company=self.company, title="Draft one",
                                             body="B")
        recorded = CompanyAction.objects.create(company=self.company,
                                                title="Recorded one", body="B")
        bc_ledger.record_action(recorded, actor=self.staff)
        response = self.client.get(self.url())
        self.assertEqual([a.pk for a in response.context["drafts"]], [draft.pk])
        self.assertEqual([a.pk for a in response.context["recorded"]], [recorded.pk])


class RecordFlowTests(ActionViewTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.staff)
        self.action = CompanyAction.objects.create(
            company=self.company, title="Adopt the 2027 plan",
            body="The board adopts the plan.", created_by=self.staff,
        )

    def test_recording_lands_on_the_new_block(self):
        response = self.client.post(
            self.url("company:action_record", self.action.uid), follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, ActionStatus.RECORDED)
        self.assertContains(response, "Adopt the 2027 plan")
        self.assertContains(response, self.action.block_uid)

    def test_the_whole_flow_end_to_end(self):
        """Draft, record, see it on the chain, verify, export."""
        self.client.post(self.url(), {
            "title": "Appoint a new head of Finance", "kind": "appointment",
            "body": "Ada is appointed.",
        }, follow=True)
        action = CompanyAction.objects.get(title="Appoint a new head of Finance")

        self.client.post(self.url("company:action_record", action.uid), follow=True)
        ledger = bc_ledger.existing_ledger(self.company)

        chain_page = self.client.get(reverse("ledger:detail", args=[ledger.uid]))
        self.assertContains(chain_page, "Appoint a new head of Finance")

        verdict = self.client.get(reverse("ledger:verify", args=[ledger.uid]))
        self.assertTrue(verdict.context["result"].ok)
        self.assertEqual(verdict.context["result"].checked, 2)

        export = self.client.get(reverse("ledger:export_xml", args=[ledger.uid]))
        self.assertIn(b"Appoint a new head of Finance", export.content)

    def test_recording_a_recorded_action_is_refused_with_a_message(self):
        self.client.post(self.url("company:action_record", self.action.uid))
        response = self.client.post(
            self.url("company:action_record", self.action.uid), follow=True,
        )
        self.assertContains(response, "Only a draft action can be recorded")

    def test_a_recorded_action_cannot_be_deleted_through_the_view(self):
        self.client.post(self.url("company:action_record", self.action.uid))
        self.client.post(self.url("company:action_delete", self.action.uid), follow=True)
        self.assertTrue(CompanyAction.objects.filter(pk=self.action.pk).exists())

    def test_the_page_carries_the_chain_verdict(self):
        self.client.post(self.url("company:action_record", self.action.uid))
        response = self.client.get(self.url())
        self.assertTrue(response.context["verification"].ok)
        self.assertIsNotNone(response.context["ledger"])

    def test_a_company_with_no_chain_yet_still_renders(self):
        response = self.client.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.context["ledger"])
