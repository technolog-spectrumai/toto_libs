"""The judging seam: the engine stores a verdict it never computes."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import governance, services
from .models import Choice, Decision, Kind, Outcome, Question, Status

User = get_user_model()


def _vote(**kwargs):
    kwargs.setdefault("title", "A vote")
    kwargs.setdefault("question_text", "Well?")
    kwargs.setdefault("kind", Kind.VOTE)
    kwargs.setdefault("closes_at", timezone.now() - timedelta(minutes=1))
    question = Question.objects.create(**kwargs)
    for position, label in enumerate(["Yes", "No"]):
        Choice.objects.create(question=question, label=label,
                              position=position)
    return question


class GovernanceSeamTests(TestCase):
    def tearDown(self):
        governance._REGISTRY.pop("test-rule", None)
        governance._SCOPE_DEFAULTS.pop("test.scope", None)

    def test_a_registered_judge_lands_in_the_decision_content(self):
        governance.register("test-rule",
                            lambda question, tally: {"verdict": "fine"})
        vote = _vote(metadata={"governance": "test-rule"})

        decision = services.record_decision(vote)

        self.assertEqual(decision.content["governance"], {"verdict": "fine"})

    def test_a_scope_with_no_judge_records_a_null_governance_block(self):
        """Polls stays threshold-free: the engine ships no judge, and the
        block says so rather than being absent."""
        vote = _vote()

        decision = services.record_decision(vote)

        self.assertIn("governance", decision.content)
        self.assertIsNone(decision.content["governance"])

    def test_the_judge_sees_the_same_tally_the_decision_stores(self):
        seen = {}

        def judge(question, tally):
            seen["ballots"] = tally.total_ballots
            return {"ok": True}

        governance.register("test-rule", judge)
        governance.register_scope_default("test.scope", "test-rule")
        vote = _vote(scope_type="test.scope", scope_id="1")

        decision = services.record_decision(vote)

        self.assertEqual(seen["ballots"], decision.total_ballots)

    def test_a_judge_that_raises_records_nothing(self):
        """A wrong decision is worse than a missing one: the transaction
        rolls the close and the row back together."""
        def broken(question, tally):
            raise RuntimeError("the rule is buggy")

        governance.register("test-rule", broken)
        vote = _vote(metadata={"governance": "test-rule"})

        with self.assertRaises(RuntimeError):
            services.record_decision(vote)

        vote.refresh_from_db()
        self.assertEqual(vote.status, Status.OPEN)
        self.assertFalse(Decision.objects.filter(question=vote).exists())

    def test_the_engine_outcome_is_unaffected_by_the_judge(self):
        governance.register("test-rule",
                            lambda question, tally: {"verdict": "passed"})
        vote = _vote(metadata={"governance": "test-rule"})

        decision = services.record_decision(vote)

        # No ballots were cast: arithmetic says NO_BALLOTS whatever the
        # judge said. Two answers, two owners, one row.
        self.assertEqual(decision.outcome, Outcome.NO_BALLOTS)

    def test_a_taken_key_refuses_a_second_judge(self):
        governance.register("test-rule", lambda question, tally: None)

        with self.assertRaises(governance.DuplicateJudge):
            governance.register("test-rule", lambda question, tally: {})
