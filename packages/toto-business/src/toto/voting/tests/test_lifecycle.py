"""Boundaries, ballot changes, and the confirmation a cast demands."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from toto.voting.models import (
    AttendanceStatus,
    Ballot,
    BallotChoice,
    MeetingStatus,
    ProposalStatus,
)
from toto.voting.services import lifecycle
from toto.voting.services.tally import tally
from toto.voting.tests.factories import VotingFactoryMixin


class LifecycleTestCase(VotingFactoryMixin, TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ada", password="x")
        self.meeting = self.make_meeting()
        self.proposition = self.make_proposition(self.meeting)
        self.entries = {e.voter_name: e for e in
                        self.make_roll(self.meeting, [("Ada", 3), ("Bob", 2)])}

    def fresh(self):
        return timezone.now()

    def cast(self, name, choice, **kwargs):
        kwargs.setdefault("confirmed_at", self.fresh())
        return lifecycle.cast_ballot(
            proposition=self.proposition, entry=self.entries[name],
            choice=choice, **kwargs,
        )


class OpeningTests(LifecycleTestCase):
    def test_a_meeting_needs_a_proposition(self):
        empty = self.make_meeting(title="Empty", slug="empty")
        self.make_roll(empty, [("Ada", 1)])
        with self.assertRaises(ValidationError):
            lifecycle.open_meeting(empty)

    def test_a_meeting_needs_an_electorate(self):
        bare = self.make_meeting(title="Bare", slug="bare")
        self.make_proposition(bare)
        with self.assertRaises(ValidationError):
            lifecycle.open_meeting(bare)

    def test_opening_freezes_the_rules_onto_the_meeting(self):
        lifecycle.open_meeting(self.meeting)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.status, MeetingStatus.OPEN)
        self.assertEqual(self.meeting.configuration_snapshot["majority_percent"], "50.00")

    def test_editing_the_configuration_afterwards_does_not_move_the_vote(self):
        """The whole reason the snapshot exists."""
        lifecycle.open_meeting(self.meeting)
        proposition = lifecycle.open_proposition(self.proposition)
        configuration = self.meeting.configuration
        configuration.majority_percent = Decimal("90.00")
        configuration.save()
        self.assertEqual(
            tally(proposition)["majority_percent"], "50.00",
        )

    def test_a_meeting_cannot_be_opened_twice(self):
        lifecycle.open_meeting(self.meeting)
        with self.assertRaises(ValidationError):
            lifecycle.open_meeting(self.meeting)

    def test_a_proposition_cannot_open_before_its_meeting(self):
        with self.assertRaises(ValidationError):
            lifecycle.open_proposition(self.proposition)

    def test_may_open_can_refuse(self):
        def refuse(meeting, user):
            raise PermissionDenied("no")

        with self.assertRaises(PermissionDenied):
            lifecycle.open_meeting(self.meeting, may_open=refuse)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.status, MeetingStatus.DRAFT)


class FrozenStateTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.open_everything(self.meeting, self.proposition)

    def test_an_open_propositions_instrument_is_frozen(self):
        self.proposition.resolution_text = "Something else entirely."
        with self.assertRaises(ValidationError):
            self.proposition.save()

    def test_an_open_propositions_attachment_hash_is_frozen(self):
        self.proposition.attachment_hash = "deadbeef"
        with self.assertRaises(ValidationError):
            self.proposition.save()

    def test_the_roll_cannot_change_after_a_ballot(self):
        self.cast("Ada", BallotChoice.FOR)
        with self.assertRaises(ValidationError):
            lifecycle.set_roll(self.meeting, [("person:ada", "Ada", Decimal("99"))],
                               source="selected")

    def test_a_voters_weight_cannot_change_after_they_vote(self):
        self.cast("Ada", BallotChoice.FOR)
        entry = self.entries["Ada"]
        entry.weight = Decimal("99")
        with self.assertRaises(ValidationError):
            entry.save()

    def test_attendance_cannot_change_after_voting_starts(self):
        self.cast("Ada", BallotChoice.FOR)
        with self.assertRaises(ValidationError):
            lifecycle.record_attendance(entry=self.entries["Bob"],
                                        status=AttendanceStatus.ABSENT)

    def test_a_voter_who_has_voted_cannot_be_removed(self):
        self.cast("Ada", BallotChoice.FOR)
        with self.assertRaises(ValidationError):
            self.entries["Ada"].delete()

    def test_a_proposition_in_voting_cannot_be_deleted(self):
        with self.assertRaises(ValidationError):
            self.proposition.delete()


class ConfirmationTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.open_everything(self.meeting, self.proposition)

    def test_a_cast_without_confirmation_is_refused(self):
        with self.assertRaises(PermissionDenied):
            lifecycle.cast_ballot(proposition=self.proposition,
                                  entry=self.entries["Ada"],
                                  choice=BallotChoice.FOR)
        self.assertFalse(Ballot.objects.exists())

    def test_a_stale_confirmation_is_refused(self):
        stale = timezone.now() - dt.timedelta(
            seconds=lifecycle.CONFIRMATION_MAX_AGE_SECONDS + 60)
        with self.assertRaises(PermissionDenied):
            self.cast("Ada", BallotChoice.FOR, confirmed_at=stale)

    def test_a_confirmation_from_the_future_is_refused(self):
        """A clock nobody controls is not evidence."""
        with self.assertRaises(PermissionDenied):
            self.cast("Ada", BallotChoice.FOR,
                      confirmed_at=timezone.now() + dt.timedelta(minutes=5))

    def test_a_fresh_confirmation_is_recorded_on_the_ballot(self):
        ballot = self.cast("Ada", BallotChoice.FOR,
                           auth_evidence={"method": "password", "ip": "10.0.0.1"})
        self.assertIsNotNone(ballot.confirmed_at)
        self.assertEqual(ballot.auth_evidence["method"], "password")


class BallotChangeTests(LifecycleTestCase):
    """The Stage 4 change: a voter may change their mind, on the record."""

    def setUp(self):
        super().setUp()
        self.open_everything(self.meeting, self.proposition)

    def test_a_second_ballot_supersedes_rather_than_being_refused(self):
        first = self.cast("Ada", BallotChoice.FOR)
        second = self.cast("Ada", BallotChoice.AGAINST)
        first.refresh_from_db()
        self.assertFalse(first.active)
        self.assertTrue(second.active)
        self.assertEqual(second.supersedes_id, first.pk)

    def test_only_the_final_ballot_counts(self):
        self.cast("Ada", BallotChoice.FOR)         # weight 3
        self.cast("Ada", BallotChoice.AGAINST)
        result = tally(self.proposition)
        self.assertEqual(result["for_weight"], "0")
        self.assertEqual(result["against_weight"], "3.000000")
        self.assertEqual(result["ballots_counted"], 1)
        self.assertEqual(result["ballots_cast_total"], 2)

    def test_every_ballot_is_kept(self):
        self.cast("Ada", BallotChoice.FOR)
        self.cast("Ada", BallotChoice.ABSTAIN)
        self.cast("Ada", BallotChoice.AGAINST)
        history = lifecycle.ballot_history(self.proposition, self.entries["Ada"])
        self.assertEqual([b.choice for b in history],
                         ["for", "abstain", "against"])
        self.assertEqual([b.active for b in history], [False, False, True])

    def test_the_database_allows_only_one_active_ballot(self):
        """The backstop, if the lock were ever lost."""
        first = self.cast("Ada", BallotChoice.FOR)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ballot.objects.create(
                proposition=self.proposition, roll_entry=self.entries["Ada"],
                choice=BallotChoice.AGAINST, weight=Decimal("3"), active=True,
            )
        del first

    def test_a_ballot_can_never_be_edited(self):
        ballot = self.cast("Ada", BallotChoice.FOR)
        ballot.choice = BallotChoice.AGAINST
        with self.assertRaises(ValidationError):
            ballot.save()

    def test_a_ballot_can_never_be_deleted(self):
        ballot = self.cast("Ada", BallotChoice.FOR)
        with self.assertRaises(ValidationError):
            ballot.delete()

    def test_superseding_may_only_ever_deactivate(self):
        """The one write a cast ballot allows, and nothing more."""
        ballot = self.cast("Ada", BallotChoice.FOR)
        ballot.choice = BallotChoice.AGAINST
        with self.assertRaises(ValidationError):
            ballot.save(_supersede=True)          # still active → refused


class EligibilityTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.open_everything(self.meeting, self.proposition)

    def test_an_absent_voter_may_not_cast(self):
        entry = self.entries["Bob"]
        entry.status = AttendanceStatus.ABSENT
        entry.save(update_fields=["status"], _allow_update=True)
        with self.assertRaises(ValidationError):
            self.cast("Bob", BallotChoice.FOR)

    def test_an_ineligible_voter_may_not_cast(self):
        entry = self.entries["Bob"]
        entry.eligible = False
        entry.save(update_fields=["eligible"], _allow_update=True)
        with self.assertRaises(ValidationError):
            self.cast("Bob", BallotChoice.FOR)

    def test_a_voter_from_another_meeting_is_refused(self):
        other = self.make_meeting(title="Other", slug="other")
        rows = self.make_roll(other, [("Zoe", 1)])
        with self.assertRaises(ValidationError):
            lifecycle.cast_ballot(proposition=self.proposition, entry=rows[0],
                                  choice=BallotChoice.FOR,
                                  confirmed_at=timezone.now())

    def test_an_unknown_choice_is_refused(self):
        with self.assertRaises(ValidationError):
            self.cast("Ada", "maybe")

    def test_may_cast_can_refuse(self):
        def refuse(entry, user):
            raise PermissionDenied("not your ballot")

        with self.assertRaises(PermissionDenied):
            self.cast("Ada", BallotChoice.FOR, may_cast=refuse)


class ClosingTests(LifecycleTestCase):
    def setUp(self):
        super().setUp()
        self.open_everything(self.meeting, self.proposition)
        self.cast("Ada", BallotChoice.FOR)

    def test_finalizing_freezes_the_result(self):
        lifecycle.finalize(self.proposition, decided_by=self.user)
        self.proposition.refresh_from_db()
        self.assertEqual(self.proposition.status, ProposalStatus.CLOSED)
        self.assertTrue(self.proposition.result_payload)
        self.assertEqual(self.proposition.result_payload["outcome"], "adopted")

    def test_a_finalized_proposition_is_immutable(self):
        lifecycle.finalize(self.proposition, decided_by=self.user)
        self.proposition.refresh_from_db()
        self.proposition.title = "Renamed"
        with self.assertRaises(ValidationError):
            self.proposition.save()

    def test_no_further_ballots_after_finalization(self):
        lifecycle.finalize(self.proposition, decided_by=self.user)
        with self.assertRaises(ValidationError):
            self.cast("Bob", BallotChoice.FOR)

    def test_finalizing_twice_is_a_no_op_not_a_second_decision(self):
        calls = []
        lifecycle.finalize(self.proposition, decided_by=self.user,
                           on_finalize=lambda p, payload: calls.append(payload))
        lifecycle.finalize(self.proposition, decided_by=self.user,
                           on_finalize=lambda p, payload: calls.append(payload))
        self.assertEqual(len(calls), 1)

    def test_the_frozen_result_names_every_voter_and_every_ballot(self):
        """Voting is never secret."""
        self.cast("Ada", BallotChoice.AGAINST)     # changes their mind
        lifecycle.finalize(self.proposition, decided_by=self.user)
        self.proposition.refresh_from_db()
        payload = self.proposition.result_payload
        self.assertEqual({row["voter"] for row in payload["roll"]}, {"Ada", "Bob"})
        self.assertEqual(len(payload["ballots"]), 2)
        self.assertEqual([b["choice"] for b in payload["ballots"]],
                         ["for", "against"])
        self.assertEqual([b["active"] for b in payload["ballots"]], [False, True])

    def test_a_failing_callback_leaves_the_vote_OPEN(self):
        """The brief's rule: if the append fails, nothing was finalized."""
        class Boom(RuntimeError):
            pass

        def explode(prop, payload):
            raise Boom("the ledger append failed")

        with self.assertRaises(Boom):
            lifecycle.finalize(self.proposition, decided_by=self.user,
                               on_finalize=explode)

        self.proposition.refresh_from_db()
        self.assertEqual(self.proposition.status, ProposalStatus.OPEN)
        self.assertEqual(self.proposition.result_payload, {})
        self.assertIsNone(self.proposition.closed_at)

    def test_a_meeting_closes_only_when_every_proposition_is_settled(self):
        with self.assertRaises(ValidationError):
            lifecycle.close_meeting(self.meeting)
        lifecycle.finalize(self.proposition, decided_by=self.user)
        lifecycle.close_meeting(self.meeting)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.status, MeetingStatus.CLOSED)
