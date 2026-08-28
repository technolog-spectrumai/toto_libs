"""Binding a vote to a Company: who may act, who may vote, and the append."""

from __future__ import annotations

from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from toto.company.integration import ledger as bc_ledger
from toto.company.integration import voting as bc_voting
from toto.company.models import CompanyMembership
from toto.company.services.ownership import issue_shares
from toto.company.tests.factories import CompanyFactoryMixin
from toto.ledger.models import LedgerEntry
from toto.ledger.services import chain
from toto.people.models import Person
from toto.voting.models import (
    AttendanceStatus,
    BallotChoice,
    Meeting,
    ProposalStatus,
    Proposition,
    RollSource,
    VotingConfiguration,
)
from toto.voting.services import lifecycle


class CompanyVotingTestCase(CompanyFactoryMixin, TestCase):
    def setUp(self):
        self.company = self.make_company()
        self.configuration = VotingConfiguration.objects.create(
            scope_type=bc_voting.SCOPE_TYPE, scope_uid=self.company.uid,
            name="Ordinary", slug="ordinary",
        )
        self.member_user = get_user_model().objects.create_user("ada", password="x")
        self.member = Person.objects.create(user=self.member_user, display_name="Ada")
        CompanyMembership.objects.create(company=self.company, person=self.member,
                                         job_title="Director")

        self.outsider_user = get_user_model().objects.create_user("zoe", password="x")
        self.outsider = Person.objects.create(user=self.outsider_user, display_name="Zoe")

    def make_company_meeting(self, **kwargs):
        fields = {
            "scope_type": bc_voting.SCOPE_TYPE,
            "scope_uid": self.company.uid,
            "title": "Annual general meeting",
            "configuration": self.configuration,
        }
        fields.update(kwargs)
        return Meeting.objects.create(**fields)


class MembershipTests(CompanyVotingTestCase):
    def test_a_member_may_open_a_vote(self):
        meeting = self.make_company_meeting()
        Proposition.objects.create(meeting=meeting, title="R1",
                                   resolution_text="Do it.")
        bc_voting.set_electorate(meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        bc_voting.open_company_meeting(meeting, opened_by=self.member_user)
        meeting.refresh_from_db()
        self.assertEqual(meeting.status, "open")

    def test_an_outsider_may_not(self):
        meeting = self.make_company_meeting()
        Proposition.objects.create(meeting=meeting, title="R1",
                                   resolution_text="Do it.")
        bc_voting.set_electorate(meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        with self.assertRaises(PermissionDenied):
            bc_voting.open_company_meeting(meeting, opened_by=self.outsider_user)

    def test_staff_are_not_exempt(self):
        """Membership is the qualification — a flag on an account is not."""
        staff = get_user_model().objects.create_user("boss", password="x",
                                                     is_staff=True, is_superuser=True)
        meeting = self.make_company_meeting()
        Proposition.objects.create(meeting=meeting, title="R1",
                                   resolution_text="Do it.")
        bc_voting.set_electorate(meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        with self.assertRaises(PermissionDenied):
            bc_voting.open_company_meeting(meeting, opened_by=staff)

    def test_a_lapsed_membership_no_longer_qualifies(self):
        membership = CompanyMembership.objects.get(company=self.company,
                                                   person=self.member)
        membership.active = False
        membership.save()
        self.assertFalse(bc_voting.is_member(self.company, self.member_user))

    def test_an_outsider_may_not_set_an_electorate(self):
        meeting = self.make_company_meeting()
        with self.assertRaises(PermissionDenied):
            bc_voting.set_electorate(meeting, source=RollSource.MEMBERS,
                                     recorded_by=self.outsider_user)


class ElectorateTests(CompanyVotingTestCase):
    def setUp(self):
        super().setUp()
        self.meeting = self.make_company_meeting()
        self.share_class = self.make_share_class(self.company)
        self.big = self.make_party(self.company, "Big Holder")
        self.small = self.make_party(self.company, "Small Holder")
        issue_shares(company=self.company, target_party=self.big,
                     share_class=self.share_class, units=Decimal("70"))
        issue_shares(company=self.company, target_party=self.small,
                     share_class=self.share_class, units=Decimal("30"))

    def test_shareholders_vote_by_weight(self):
        bc_voting.set_electorate(self.meeting, source=RollSource.SHAREHOLDERS,
                                 recorded_by=self.member_user)
        rows = {e.voter_name: e.weight for e in self.meeting.roll.all()}
        self.assertEqual(rows, {"Big Holder": Decimal("70.000000"),
                                "Small Holder": Decimal("30.000000")})

    def test_a_weighted_share_class_carries_its_votes(self):
        preferred = self.make_share_class(self.company, name="Preferred",
                                          slug="preferred",
                                          votes_per_unit=Decimal("10"))
        issue_shares(company=self.company, target_party=self.small,
                     share_class=preferred, units=Decimal("5"))
        bc_voting.set_electorate(self.meeting, source=RollSource.SHAREHOLDERS,
                                 recorded_by=self.member_user)
        rows = {e.voter_name: e.weight for e in self.meeting.roll.all()}
        self.assertEqual(rows["Small Holder"], Decimal("80.000000"))   # 30 + 5×10

    def test_members_vote_one_each(self):
        other = Person.objects.create(display_name="Bob")
        CompanyMembership.objects.create(company=self.company, person=other)
        bc_voting.set_electorate(self.meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        rows = {e.voter_name: e.weight for e in self.meeting.roll.all()}
        self.assertEqual(rows, {"Ada": Decimal("1.000000"),
                                "Bob": Decimal("1.000000")})

    def test_manually_selected_people_vote_one_each(self):
        bc_voting.set_electorate(self.meeting, source=RollSource.SELECTED,
                                 recorded_by=self.member_user,
                                 people=[self.member, self.outsider])
        rows = {e.voter_name: e.weight for e in self.meeting.roll.all()}
        self.assertEqual(rows, {"Ada": Decimal("1.000000"),
                                "Zoe": Decimal("1.000000")})

    def test_a_selected_person_need_not_be_a_member(self):
        """The brief allows hand-picking any existing person."""
        bc_voting.set_electorate(self.meeting, source=RollSource.SELECTED,
                                 recorded_by=self.member_user,
                                 people=[self.outsider])
        self.assertEqual(self.meeting.roll.count(), 1)

    def test_changing_the_electorate_replaces_the_whole_roll(self):
        bc_voting.set_electorate(self.meeting, source=RollSource.SHAREHOLDERS,
                                 recorded_by=self.member_user)
        bc_voting.set_electorate(self.meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        self.assertEqual({e.voter_name for e in self.meeting.roll.all()}, {"Ada"})

    def test_the_roll_is_a_COPY_and_does_not_move_with_the_register(self):
        """A shareholder who sells up after the vote was still entitled then."""
        bc_voting.set_electorate(self.meeting, source=RollSource.SHAREHOLDERS,
                                 recorded_by=self.member_user)
        before = {e.voter_name: e.weight for e in self.meeting.roll.all()}
        from toto.company.services.ownership import transfer_shares

        transfer_shares(company=self.company, source_party=self.big,
                        target_party=self.small, share_class=self.share_class,
                        units=Decimal("70"))
        after = {e.voter_name: e.weight for e in self.meeting.roll.all()}
        self.assertEqual(before, after)


class CastingAuthorizationTests(CompanyVotingTestCase):
    def setUp(self):
        super().setUp()
        self.meeting = self.make_company_meeting()
        self.proposition = Proposition.objects.create(
            meeting=self.meeting, title="R1", resolution_text="Do it.")
        bc_voting.set_electorate(self.meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        for entry in self.meeting.roll.all():
            entry.status = AttendanceStatus.PRESENT
            entry.save(update_fields=["status"], _allow_update=True)
        bc_voting.open_company_meeting(self.meeting, opened_by=self.member_user)
        lifecycle.open_proposition(self.proposition)
        self.entry = self.meeting.roll.get()

    def test_the_named_voter_may_cast(self):
        ballot = bc_voting.cast(proposition=self.proposition, entry=self.entry,
                                choice=BallotChoice.FOR, cast_by=self.member_user,
                                confirmed_at=timezone.now())
        self.assertEqual(ballot.choice, "for")

    def test_somebody_else_may_not_cast_it(self):
        other_user = get_user_model().objects.create_user("bob", password="x")
        other = Person.objects.create(user=other_user, display_name="Bob")
        CompanyMembership.objects.create(company=self.company, person=other)
        with self.assertRaises(PermissionDenied):
            bc_voting.cast(proposition=self.proposition, entry=self.entry,
                           choice=BallotChoice.FOR, cast_by=other_user,
                           confirmed_at=timezone.now())

    def test_a_recorded_representative_may(self):
        other_user = get_user_model().objects.create_user("bob", password="x")
        other = Person.objects.create(user=other_user, display_name="Bob")
        self.entry.represented_by = bc_voting.user_ref(other_user)
        self.entry.save(update_fields=["represented_by"], _allow_update=True)
        ballot = bc_voting.cast(proposition=self.proposition, entry=self.entry,
                                choice=BallotChoice.FOR, cast_by=other_user,
                                confirmed_at=timezone.now())
        self.assertEqual(ballot.cast_by, other_user)
        self.assertEqual(ballot.roll_entry.voter_name, "Ada")

    def test_anonymous_may_not_cast(self):
        with self.assertRaises(PermissionDenied):
            bc_voting.cast(proposition=self.proposition, entry=self.entry,
                           choice=BallotChoice.FOR, cast_by=None,
                           confirmed_at=timezone.now())


class FinalizationTests(CompanyVotingTestCase):
    def setUp(self):
        super().setUp()
        self.meeting = self.make_company_meeting()
        self.proposition = Proposition.objects.create(
            meeting=self.meeting, title="Adopt the 2027 plan",
            resolution_text="That the plan be adopted.")
        bc_voting.set_electorate(self.meeting, source=RollSource.MEMBERS,
                                 recorded_by=self.member_user)
        for entry in self.meeting.roll.all():
            entry.status = AttendanceStatus.PRESENT
            entry.save(update_fields=["status"], _allow_update=True)
        bc_voting.open_company_meeting(self.meeting, opened_by=self.member_user)
        lifecycle.open_proposition(self.proposition)
        self.entry = self.meeting.roll.get()
        bc_voting.cast(proposition=self.proposition, entry=self.entry,
                       choice=BallotChoice.FOR, cast_by=self.member_user,
                       confirmed_at=timezone.now())

    def test_finalizing_appends_the_decision_to_the_company_chain(self):
        bc_voting.finalize_proposition(self.proposition, decided_by=self.member_user)
        ledger = bc_ledger.existing_ledger(self.company)
        entry = ledger.entries.get(source_type="voting.proposition")
        self.assertEqual(entry.source_ref, "Adopt the 2027 plan")
        self.assertIn("Adopt the 2027 plan", entry.payload_xml)
        self.assertIn("adopted", entry.payload_xml)

    def test_the_block_id_is_written_back_onto_the_proposition(self):
        bc_voting.finalize_proposition(self.proposition, decided_by=self.member_user)
        self.proposition.refresh_from_db()
        ledger = bc_ledger.existing_ledger(self.company)
        entry = ledger.entries.get(source_type="voting.proposition")
        self.assertEqual(self.proposition.block_uid, entry.uid)

    def test_the_block_names_every_voter(self):
        bc_voting.finalize_proposition(self.proposition, decided_by=self.member_user)
        ledger = bc_ledger.existing_ledger(self.company)
        entry = ledger.entries.get(source_type="voting.proposition")
        self.assertIn("Ada", entry.payload_xml)

    def test_the_chain_still_verifies_afterwards(self):
        bc_voting.finalize_proposition(self.proposition, decided_by=self.member_user)
        self.assertTrue(bc_ledger.verify_company_ledger(self.company).ok)

    def test_an_outsider_may_not_finalize(self):
        with self.assertRaises(PermissionDenied):
            bc_voting.finalize_proposition(self.proposition,
                                           decided_by=self.outsider_user)
        self.proposition.refresh_from_db()
        self.assertEqual(self.proposition.status, ProposalStatus.OPEN)

    def test_A_FAILED_APPEND_LEAVES_THE_VOTE_UNFINALIZED(self):
        """The brief's rule, and the reason finalize takes a callback.

        Forces the append to fail and asserts BOTH halves rolled back: no block
        on the chain, and the proposition still open with no frozen result.
        """
        ledger = bc_ledger.company_ledger(self.company)
        before = ledger.entries.count()

        class Boom(RuntimeError):
            pass

        with mock.patch.object(chain, "append", side_effect=Boom("chain locked")):
            with self.assertRaises(Boom):
                bc_voting.finalize_proposition(self.proposition,
                                               decided_by=self.member_user)

        self.proposition.refresh_from_db()
        self.assertEqual(self.proposition.status, ProposalStatus.OPEN)
        self.assertEqual(self.proposition.result_payload, {})
        self.assertIsNone(self.proposition.block_uid)
        self.assertEqual(ledger.entries.count(), before)
        self.assertTrue(bc_ledger.verify_company_ledger(self.company).ok)

    def test_finalizing_twice_appends_one_block_not_two(self):
        bc_voting.finalize_proposition(self.proposition, decided_by=self.member_user)
        bc_voting.finalize_proposition(self.proposition, decided_by=self.member_user)
        ledger = bc_ledger.existing_ledger(self.company)
        self.assertEqual(
            ledger.entries.filter(source_type="voting.proposition").count(), 1)


class BoundaryTests(TestCase):
    def test_voting_has_no_relation_into_company_or_ledger(self):
        from toto.voting.models import Ballot, RollEntry

        for model in (Ballot, RollEntry, Meeting, Proposition):
            labels = {
                f.related_model._meta.app_label
                for f in model._meta.get_fields()
                if f.is_relation and f.related_model is not None
            }
            with self.subTest(model=model.__name__):
                self.assertNotIn("company", labels)
                self.assertNotIn("ledger", labels)

    def test_the_ledger_gained_no_relation_into_voting(self):
        labels = {
            f.related_model._meta.app_label
            for f in LedgerEntry._meta.get_fields()
            if f.is_relation and f.related_model is not None
        }
        self.assertNotIn("voting", labels)
