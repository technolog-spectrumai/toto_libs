"""Binding a chain to a Company, and recording an Action onto it."""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase

from toto.company.integration import ledger as bc_ledger
from toto.company.models import (
    ActionStatus,
    CompanyAction,
    CompanyMembership,
    Department,
)
from toto.company.services.ownership import issue_shares
from toto.company.tests.factories import CompanyFactoryMixin
from toto.ledger.models import Ledger, LedgerEntry
from toto.ledger.services import chain
from toto.people.models import Person


class ProvisioningTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()

    def test_a_company_gets_exactly_one_chain(self):
        first = bc_ledger.company_ledger(self.company)
        second = bc_ledger.company_ledger(self.company)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Ledger.objects.count(), 1)

    def test_the_chain_opens_with_a_genesis_block(self):
        ledger = bc_ledger.company_ledger(self.company)
        self.assertEqual(ledger.entries.count(), 1)
        self.assertTrue(ledger.entries.get().is_genesis)

    def test_two_companies_get_two_chains(self):
        other = self.make_company("Other sp. z o.o.")
        bc_ledger.company_ledger(self.company)
        bc_ledger.company_ledger(other)
        self.assertEqual(Ledger.objects.count(), 2)

    def test_the_chain_names_its_company_by_uid_not_by_foreign_key(self):
        ledger = bc_ledger.company_ledger(self.company)
        self.assertEqual(ledger.scope_type, "company.company")
        self.assertEqual(ledger.scope_uid, self.company.uid)
        field_names = {f.name for f in Ledger._meta.get_fields()}
        self.assertNotIn("company", field_names)

    def test_the_company_can_be_found_back_from_the_chain(self):
        ledger = bc_ledger.company_ledger(self.company)
        self.assertEqual(bc_ledger.company_for(ledger), self.company)

    def test_a_chain_whose_company_is_gone_resolves_to_nothing(self):
        ledger = bc_ledger.company_ledger(self.company)
        ledger.scope_uid = self.make_company("Ghost").uid
        ledger.save(update_fields=["scope_uid"])
        Ledger.objects.filter(pk=ledger.pk).update(
            scope_uid="00000000-0000-0000-0000-000000000000",
        )
        ledger.refresh_from_db()
        self.assertIsNone(bc_ledger.company_for(ledger))

    def test_backfill_opens_a_chain_for_every_company_and_is_idempotent(self):
        self.make_company("Two")
        self.make_company("Three")
        self.assertEqual(bc_ledger.backfill(), 3)
        self.assertEqual(bc_ledger.backfill(), 0)
        self.assertEqual(Ledger.objects.count(), 3)

    def test_existing_ledger_does_not_create_one(self):
        self.assertIsNone(bc_ledger.existing_ledger(self.company))
        self.assertEqual(Ledger.objects.count(), 0)


class RecordingTests(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("boss", password="x")
        self.company = self.make_company()
        self.finance = Department.objects.create(company=self.company, name="Finance")
        person = Person.objects.create(display_name="Ada Lovelace")
        CompanyMembership.objects.create(
            company=self.company, person=person, job_title="Chief Engineer",
            primary_department=self.finance,
        )
        self.share_class = self.make_share_class(self.company)
        self.party = self.make_party(self.company, "Ada")
        issue_shares(company=self.company, target_party=self.party,
                     share_class=self.share_class, units=Decimal("100"))
        self.action = CompanyAction.objects.create(
            company=self.company, title="Adopt the 2027 plan",
            body="The board adopts the plan.", created_by=self.user,
        )

    def test_recording_appends_one_block_and_marks_the_action(self):
        entry = bc_ledger.record_action(self.action, actor=self.user)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, ActionStatus.RECORDED)
        self.assertEqual(self.action.block_uid, entry.uid)
        self.assertEqual(entry.sequence, 2)          # genesis is 1

    def test_the_block_freezes_the_complete_company_facts(self):
        entry = bc_ledger.record_action(self.action, actor=self.user)
        payload = entry.payload_xml
        self.assertIn("Adopt the 2027 plan", payload)
        self.assertIn(self.company.name, payload)
        self.assertIn("Finance", payload)            # department
        self.assertIn("Ada Lovelace", payload)       # member
        self.assertIn("Chief Engineer", payload)     # job title
        self.assertIn("100.000000", payload)         # shareholding, exact

    def test_the_frozen_facts_do_not_move_when_the_company_does(self):
        entry = bc_ledger.record_action(self.action, actor=self.user)
        before = entry.payload_xml

        self.company.name = "Renamed Holdings"
        self.company.save()
        self.finance.name = "Treasury"
        self.finance.save()

        entry.refresh_from_db()
        self.assertEqual(entry.payload_xml, before)
        self.assertIn("Farfarele", entry.payload_xml)
        self.assertTrue(bc_ledger.verify_company_ledger(self.company))

    def test_a_recorded_action_is_frozen(self):
        bc_ledger.record_action(self.action, actor=self.user)
        self.action.refresh_from_db()
        self.action.title = "Something else"
        with self.assertRaises(ValidationError):
            self.action.save()
        with self.assertRaises(ValidationError):
            self.action.delete()

    def test_a_draft_can_still_be_edited_and_deleted(self):
        self.action.title = "Adopt the 2027 plan (revised)"
        self.action.save()
        self.action.delete()
        self.assertFalse(CompanyAction.objects.filter(pk=self.action.pk).exists())

    def test_recording_twice_is_refused(self):
        bc_ledger.record_action(self.action, actor=self.user)
        self.action.refresh_from_db()
        with self.assertRaises(ValueError):
            bc_ledger.record_action(self.action, actor=self.user)

    def test_a_failed_append_leaves_the_action_a_draft(self):
        """Atomicity, from the caller's side: no block, no status change."""
        class Boom(RuntimeError):
            pass

        def exploding_signer(block_text):
            raise Boom("the signer failed")

        ledger = bc_ledger.company_ledger(self.company)
        before = ledger.entries.count()

        with self.assertRaises(Boom):
            bc_ledger.record_action(self.action, actor=self.user,
                                    signer=exploding_signer)

        self.action.refresh_from_db()
        self.assertEqual(self.action.status, ActionStatus.DRAFT)
        self.assertIsNone(self.action.block_uid)
        self.assertEqual(ledger.entries.count(), before)

    def test_an_action_citing_an_earlier_block_stays_ordinary_text(self):
        first = bc_ledger.record_action(self.action, actor=self.user)
        second = CompanyAction.objects.create(
            company=self.company, title="Correct the 2027 plan",
            body=f"This corrects block {first.uid}.", created_by=self.user,
        )
        entry = bc_ledger.record_action(second, actor=self.user)
        self.assertIn(str(first.uid), entry.payload_xml)
        field_names = {f.name for f in CompanyAction._meta.get_fields()}
        self.assertNotIn("supersedes", field_names)
        self.assertNotIn("corrects", field_names)

    def test_the_chain_verifies_after_several_actions(self):
        bc_ledger.record_action(self.action, actor=self.user)
        for n in range(3):
            action = CompanyAction.objects.create(
                company=self.company, title=f"Action {n}", body="body",
                created_by=self.user,
            )
            bc_ledger.record_action(action, actor=self.user)
        result = bc_ledger.verify_company_ledger(self.company)
        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 5)

    def test_a_company_with_no_chain_verifies_vacuously(self):
        other = self.make_company("Untouched sp. z o.o.")
        result = bc_ledger.verify_company_ledger(other)
        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 0)


class BoundaryTests(TestCase):
    def test_the_ledger_app_has_no_reverse_relation_into_company(self):
        related = {
            f.name for f in LedgerEntry._meta.get_fields()
            if f.is_relation and f.related_model is not None
        }
        models = {
            f.related_model._meta.app_label for f in LedgerEntry._meta.get_fields()
            if f.is_relation and f.related_model is not None
        }
        self.assertNotIn("company", models, f"ledger relates to company via {related}")
