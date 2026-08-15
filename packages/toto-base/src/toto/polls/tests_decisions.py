"""Recording a decision: written once, complete, and immune to hindsight."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone

from . import services
from .core import Eligibility
from .models import Ballot, Choice, Decision, Kind, Outcome, Question, Status

User = get_user_model()


def _vote(**kwargs):
    kwargs.setdefault("title", "A vote")
    kwargs.setdefault("question_text", "Well?")
    kwargs.setdefault("kind", Kind.VOTE)
    kwargs.setdefault("closes_at", timezone.now() + timedelta(hours=1))
    question = Question.objects.create(**kwargs)
    for position, label in enumerate(["Yes", "No"]):
        Choice.objects.create(question=question, label=label,
                              position=position)
    return question


def _expire(question):
    """Move the deadline into the past, the way time does."""
    Question.objects.filter(pk=question.pk).update(
        closes_at=timezone.now() - timedelta(minutes=1))
    question.refresh_from_db()


class _Weighted:
    def __init__(self, weights):
        self.weights = weights

    def standing(self, question, user):
        return Eligibility(True, weight=self.weights.get(user.username, 1))

    def size(self, question):
        return len(self.weights)


class RecordDecisionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.voters = [User.objects.create_user(f"v{i}", password="pw")
                       for i in range(3)]

    def test_recording_closes_and_snapshots_everything(self):
        vote = _vote(created_by=self.staff)
        yes, no = vote.choices.all()
        services.cast(vote, self.voters[0], yes)
        services.cast(vote, self.voters[1], yes)
        services.cast(vote, self.voters[2], no)
        _expire(vote)

        decision = services.record_decision(vote)

        vote.refresh_from_db()
        self.assertEqual(vote.status, Status.CLOSED)
        self.assertEqual(decision.outcome, Outcome.WINNER)
        self.assertEqual(decision.winner_label, "Yes")
        self.assertEqual(decision.total_ballots, 3)
        content = decision.content
        self.assertEqual(content["proposer"]["username"], "op")
        self.assertEqual(len(content["ballots"]), 3)
        self.assertEqual(len(content["tally"]), 2)
        self.assertEqual(content["electorate"]["key"], "all")
        self.assertIn("opens_at", content["window"])
        # An overdue vote closed AT its deadline, not when recording ran.
        self.assertEqual(vote.closed_at, vote.closes_at)

    def test_recording_is_idempotent(self):
        vote = _vote()
        _expire(vote)

        first = services.record_decision(vote)
        second = services.record_decision(vote)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(Decision.objects.count(), 1)

    def test_a_tie_is_a_real_outcome(self):
        vote = _vote()
        yes, no = vote.choices.all()
        services.cast(vote, self.voters[0], yes)
        services.cast(vote, self.voters[1], no)
        _expire(vote)

        decision = services.record_decision(vote)

        self.assertEqual(decision.outcome, Outcome.TIE)
        self.assertEqual(decision.winner_label, "")

    def test_no_ballots_is_a_real_outcome(self):
        vote = _vote()
        _expire(vote)

        self.assertEqual(services.record_decision(vote).outcome,
                         Outcome.NO_BALLOTS)

    def test_the_outcome_weighs_weight_not_heads(self):
        vote = _vote()
        yes, no = vote.choices.all()
        roll = _Weighted({"v0": 5, "v1": 1, "v2": 1})
        services.cast(vote, self.voters[0], yes, electorate=roll)
        services.cast(vote, self.voters[1], no, electorate=roll)
        services.cast(vote, self.voters[2], no, electorate=roll)
        _expire(vote)

        decision = services.record_decision(vote)

        self.assertEqual(decision.winner_label, "Yes")
        self.assertEqual(decision.total_weight, 7)

    def test_a_poll_is_not_recorded(self):
        poll = _vote(kind=Kind.POLL)
        _expire(poll)

        with self.assertRaises(ValueError):
            services.record_decision(poll)


class HistoricalIntegrityTests(TestCase):
    def setUp(self):
        self.voter = User.objects.create_user("v", password="pw")
        self.vote = _vote()
        services.cast(self.vote, self.voter, self.vote.choices.first())
        _expire(self.vote)
        self.decision = services.record_decision(self.vote)

    def test_deleting_ballots_does_not_rewrite_the_decision(self):
        """The queryset delete stays possible (the trust model) — and the
        decision must not notice."""
        Ballot.objects.filter(question=self.vote).delete()

        self.decision.refresh_from_db()
        self.assertEqual(self.decision.total_ballots, 1)
        self.assertEqual(len(self.decision.content["ballots"]), 1)

    def test_a_decided_question_is_undeletable(self):
        with self.assertRaises(ProtectedError):
            self.vote.delete()

    def test_a_decision_is_never_edited_or_deleted(self):
        with self.assertRaises(ValueError):
            self.decision.save()
        with self.assertRaises(ValueError):
            self.decision.delete()


class EarlyCloseTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.civilian = User.objects.create_user("civ", password="pw")

    def test_early_close_is_a_staff_act(self):
        vote = _vote()  # deadline an hour away

        with self.assertRaises(PermissionDenied):
            services.record_decision(vote)
        with self.assertRaises(PermissionDenied):
            services.record_decision(vote, decided_by=self.civilian)

    def test_staff_early_close_records_and_signs(self):
        vote = _vote()

        decision = services.record_decision(vote, decided_by=self.staff)

        self.assertEqual(decision.decided_by, self.staff)
        vote.refresh_from_db()
        self.assertEqual(vote.status, Status.CLOSED)

    def test_a_vote_with_no_deadline_needs_staff_too(self):
        vote = _vote(closes_at=None)

        with self.assertRaises(PermissionDenied):
            services.record_decision(vote)
        self.assertIsNotNone(
            services.record_decision(vote, decided_by=self.staff))


class RecordOverdueTests(TestCase):
    def test_the_sweep_records_exactly_the_overdue_votes(self):
        overdue = _vote(title="Overdue")
        _expire(overdue)
        running = _vote(title="Still running")
        no_deadline = _vote(title="Manual close only", closes_at=None)
        poll = _vote(title="A poll", kind=Kind.POLL)
        _expire(poll)
        scoped = _vote(title="Community vote", scope_type="socialhub.community",
                       scope_id="1")
        _expire(scoped)

        recorded = services.record_overdue()

        self.assertEqual(recorded, 1)
        self.assertTrue(Decision.objects.filter(question=overdue).exists())
        for question in (running, no_deadline, poll, scoped):
            self.assertFalse(
                Decision.objects.filter(question=question).exists())

    def test_the_sweep_is_idempotent(self):
        vote = _vote()
        _expire(vote)

        self.assertEqual(services.record_overdue(), 1)
        self.assertEqual(services.record_overdue(), 0)
