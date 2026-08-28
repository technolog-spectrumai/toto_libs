"""The rule matrix. Every branch here exists because some articles say it."""

from __future__ import annotations

from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from toto.voting.models import (
    AbstentionRule,
    AttendanceStatus,
    BallotChoice,
    MajorityBasis,
    QuorumBasis,
    ThresholdMode,
)
from toto.voting.services import lifecycle
from toto.voting.services.tally import tally
from toto.voting.tests.factories import VotingFactoryMixin


class TallyTestCase(VotingFactoryMixin, TestCase):
    def vote(self, rows, ballots, *, present=None, **config):
        """Build a whole vote from a rule set and a set of ballots."""
        configuration = self.make_configuration(**config)
        meeting = self.make_meeting(configuration)
        proposition = self.make_proposition(meeting)
        entries = {entry.voter_name: entry for entry in
                   self.make_roll(meeting, rows)}
        if present is not None:
            for entry in entries.values():
                entry.status = (AttendanceStatus.PRESENT if entry.voter_name in present
                                else AttendanceStatus.ABSENT)
                entry.save(update_fields=["status"], _allow_update=True)
        self.open_everything(meeting, proposition)
        for name, choice in ballots.items():
            lifecycle.cast_ballot(
                proposition=proposition, entry=entries[name], choice=choice,
                confirmed_at=timezone.now(),
            )
        return tally(proposition)


class MajorityBasisTests(TallyTestCase):
    ROWS = [("Ada", 50), ("Bob", 30), ("Cleo", 20)]

    def test_cast_counts_every_ballot_including_abstentions(self):
        result = self.vote(
            self.ROWS,
            {"Ada": BallotChoice.FOR, "Bob": BallotChoice.AGAINST,
             "Cleo": BallotChoice.ABSTAIN},
            majority_basis=MajorityBasis.CAST,
        )
        self.assertEqual(result["denominator_weight"], "100.000000")
        self.assertEqual(result["achieved_percent"], "50.00")
        self.assertFalse(result["majority_met"])       # strict: 50 is not > 50

    def test_decisive_ignores_abstentions_entirely(self):
        result = self.vote(
            self.ROWS,
            {"Ada": BallotChoice.FOR, "Bob": BallotChoice.AGAINST,
             "Cleo": BallotChoice.ABSTAIN},
            majority_basis=MajorityBasis.DECISIVE,
        )
        self.assertEqual(result["denominator_weight"], "80.000000")
        self.assertEqual(result["achieved_percent"], "62.50")
        self.assertTrue(result["majority_met"])

    def test_present_measures_against_the_room(self):
        result = self.vote(
            self.ROWS, {"Ada": BallotChoice.FOR},
            present=["Ada", "Bob"], majority_basis=MajorityBasis.PRESENT,
        )
        self.assertEqual(result["denominator_weight"], "80.000000")
        self.assertEqual(result["achieved_percent"], "62.50")

    def test_eligible_measures_against_everyone_entitled(self):
        result = self.vote(
            self.ROWS, {"Ada": BallotChoice.FOR},
            present=["Ada", "Bob"], majority_basis=MajorityBasis.ELIGIBLE,
        )
        self.assertEqual(result["denominator_weight"], "100.000000")
        self.assertEqual(result["achieved_percent"], "50.00")


class AbstentionRuleTests(TallyTestCase):
    ROWS = [("Ada", 60), ("Bob", 20), ("Cleo", 20)]
    BALLOTS = {"Ada": BallotChoice.FOR, "Bob": BallotChoice.AGAINST,
               "Cleo": BallotChoice.ABSTAIN}

    def test_include_leaves_abstentions_in_the_denominator(self):
        result = self.vote(self.ROWS, self.BALLOTS,
                           abstention_rule=AbstentionRule.INCLUDE)
        self.assertEqual(result["denominator_weight"], "100.000000")
        self.assertEqual(result["achieved_percent"], "60.00")

    def test_exclude_takes_them_out(self):
        result = self.vote(self.ROWS, self.BALLOTS,
                           abstention_rule=AbstentionRule.EXCLUDE)
        self.assertEqual(result["denominator_weight"], "80.000000")
        self.assertEqual(result["achieved_percent"], "75.00")

    def test_against_counts_them_as_opposition(self):
        result = self.vote(self.ROWS, self.BALLOTS,
                           abstention_rule=AbstentionRule.AGAINST,
                           majority_basis=MajorityBasis.DECISIVE)
        self.assertEqual(result["counted_against_weight"], "40.000000")
        self.assertEqual(result["denominator_weight"], "100.000000")
        self.assertEqual(result["achieved_percent"], "60.00")


class ThresholdModeTests(TallyTestCase):
    ROWS = [("Ada", 50), ("Bob", 50)]
    BALLOTS = {"Ada": BallotChoice.FOR, "Bob": BallotChoice.AGAINST}

    def test_strict_refuses_an_exact_tie_at_the_threshold(self):
        result = self.vote(self.ROWS, self.BALLOTS,
                           threshold_mode=ThresholdMode.STRICT)
        self.assertEqual(result["achieved_percent"], "50.00")
        self.assertFalse(result["majority_met"])

    def test_inclusive_accepts_it(self):
        result = self.vote(self.ROWS, self.BALLOTS,
                           threshold_mode=ThresholdMode.INCLUSIVE)
        self.assertTrue(result["majority_met"])


class QuorumTests(TallyTestCase):
    ROWS = [("Ada", 60), ("Bob", 20), ("Cleo", 20)]

    def test_quorum_on_weight(self):
        result = self.vote(self.ROWS, {"Ada": BallotChoice.FOR},
                           present=["Ada"], quorum_basis=QuorumBasis.WEIGHT,
                           quorum_percent=Decimal("50"))
        self.assertEqual(result["present_weight"], "60.000000")
        self.assertTrue(result["quorum_met"])          # 60% > 50%

    def test_quorum_on_heads_can_disagree_with_quorum_on_weight(self):
        """One holder with most of the weight is not most of the room."""
        result = self.vote(self.ROWS, {"Ada": BallotChoice.FOR},
                           present=["Ada"], quorum_basis=QuorumBasis.MEMBERS,
                           quorum_percent=Decimal("50"))
        self.assertEqual(result["present_voters"], 1)
        self.assertEqual(result["eligible_voters"], 3)
        self.assertFalse(result["quorum_met"])         # 33% is not > 50%

    def test_without_quorum_nothing_is_adopted_however_the_vote_went(self):
        result = self.vote(self.ROWS, {"Ada": BallotChoice.FOR},
                           present=["Ada"], quorum_basis=QuorumBasis.MEMBERS,
                           quorum_percent=Decimal("90"))
        self.assertTrue(result["majority_met"])
        self.assertFalse(result["quorum_met"])
        self.assertFalse(result["adopted"])


class ExactnessTests(TallyTestCase):
    def test_fractional_weights_are_exact(self):
        result = self.vote(
            [("Ada", "0.000002"), ("Bob", "0.000001")],
            {"Ada": BallotChoice.FOR, "Bob": BallotChoice.AGAINST},
        )
        self.assertEqual(result["for_weight"], "0.000002")
        self.assertEqual(result["denominator_weight"], "0.000003")

    def test_a_vote_with_no_ballots_divides_by_nothing(self):
        result = self.vote([("Ada", 10)], {})
        self.assertEqual(result["denominator_weight"], "0")
        self.assertEqual(result["achieved_percent"], "0.00")
        self.assertFalse(result["majority_met"])
        self.assertFalse(result["adopted"])

    def test_the_tally_is_deterministic(self):
        configuration = self.make_configuration()
        meeting = self.make_meeting(configuration)
        proposition = self.make_proposition(meeting)
        entries = {e.voter_name: e for e in self.make_roll(
            meeting, [("Ada", 3), ("Bob", 2)])}
        self.open_everything(meeting, proposition)
        for name, choice in (("Ada", BallotChoice.FOR), ("Bob", BallotChoice.AGAINST)):
            lifecycle.cast_ballot(proposition=proposition, entry=entries[name],
                                  choice=choice, confirmed_at=timezone.now())
        self.assertEqual(tally(proposition), tally(proposition))
