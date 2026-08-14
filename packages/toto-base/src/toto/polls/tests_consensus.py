"""Stage 7: named consensus rules, snapshotted, and early finalization."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from . import services
from .electorate_models import ConsensusProfile
from .models import Choice, Decision, Kind, Question

User = get_user_model()


def _profile(name="Supermajority", percent="75.00"):
    return ConsensusProfile.objects.create(name=name,
                                           percent=Decimal(percent))


def _vote(entries, profile=None, **kwargs):
    kwargs.setdefault("title", "A resolution")
    kwargs.setdefault("question_text", "Well?")
    kwargs.setdefault("kind", Kind.VOTE)
    kwargs.setdefault("closes_at", timezone.now() + timedelta(days=1))
    question = Question.objects.create(**kwargs)
    Choice.objects.create(question=question, label="For", value=1, position=0)
    Choice.objects.create(question=question, label="Against", value=-1,
                          position=1)
    Choice.objects.create(question=question, label="Abstain", value=0,
                          position=2)
    if profile is not None:
        services.snapshot_rule(question, profile)
    services.freeze_roll(question, entries=entries)
    return question


def _choice(question, label):
    return question.choices.get(label=label)


def _expire(question):
    Question.objects.filter(pk=question.pk).update(
        closes_at=timezone.now() - timedelta(minutes=1))
    question.refresh_from_db()


class ProfileSnapshotTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")

    def test_a_vote_snapshots_the_name_and_the_number(self):
        profile = _profile()
        question = _vote([(self.a, "A", 1)], profile)

        self.assertEqual(question.rule_name, "Supermajority")
        self.assertEqual(question.rule_percent, Decimal("75.00"))

    def test_retuning_a_profile_never_rewrites_a_past_vote(self):
        profile = _profile()
        question = _vote([(self.a, "A", 1)], profile)

        profile.name = "Renamed"
        profile.percent = Decimal("50.00")
        profile.save()

        question.refresh_from_db()
        self.assertEqual(question.rule_name, "Supermajority")
        self.assertEqual(question.rule_percent, Decimal("75.00"))

    def test_the_rule_locks_once_voting_starts(self):
        question = _vote([(self.a, "A", 1)], _profile())

        question.rule_percent = Decimal("10.00")
        with self.assertRaises(ValueError):
            question.save()


class EvaluationTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")
        self.c = User.objects.create_user("c", password="pw")

    def _weighted_vote(self, profile=None):
        return _vote([(self.a, "A", 60), (self.b, "B", 30),
                      (self.c, "C", 10)], profile)

    def test_adoption_needs_strictly_more_than_the_bar(self):
        """Exactly the threshold does not adopt — the same strict comparison
        the company constitution has always made."""
        question = self._weighted_vote(_profile(percent="60.00"))
        services.cast(question, self.a, _choice(question, "For"))     # 60
        services.cast(question, self.b, _choice(question, "Against"))  # 30
        services.cast(question, self.c, _choice(question, "Against"))  # 10
        _expire(question)

        decision = services.record_decision(question)

        self.assertEqual(decision.content["consensus"]["achieved_percent"],
                         60.0)
        self.assertFalse(decision.adopted)

    def test_clearing_the_bar_adopts(self):
        question = self._weighted_vote(_profile(percent="50.00"))
        services.cast(question, self.a, _choice(question, "For"))
        services.cast(question, self.b, _choice(question, "Against"))
        _expire(question)

        decision = services.record_decision(question)

        consensus = decision.content["consensus"]
        self.assertTrue(decision.adopted)
        self.assertEqual(consensus["for_weight"], 60)
        self.assertEqual(consensus["against_weight"], 30)
        self.assertEqual(consensus["name"], "Supermajority")

    def test_abstentions_leave_the_denominator(self):
        question = self._weighted_vote(_profile(percent="75.00"))
        services.cast(question, self.a, _choice(question, "For"))      # 60
        services.cast(question, self.b, _choice(question, "Abstain"))  # 30
        services.cast(question, self.c, _choice(question, "Against"))  # 10
        _expire(question)

        decision = services.record_decision(question)

        # 60 of 70 decided = 85.7%, not 60 of 100.
        self.assertEqual(decision.content["consensus"]["denominator_weight"],
                         70)
        self.assertTrue(decision.adopted)

    def test_nothing_decided_is_not_adoption(self):
        question = self._weighted_vote(_profile())
        _expire(question)

        decision = services.record_decision(question)

        self.assertFalse(decision.adopted)
        self.assertEqual(decision.content["consensus"]["achieved_percent"],
                         0.0)

    def test_a_vote_without_a_rule_records_no_consensus(self):
        question = self._weighted_vote()
        _expire(question)

        decision = services.record_decision(question)

        self.assertIsNone(decision.content["consensus"])
        self.assertIsNone(decision.adopted)


class EarlyFinalizationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")
        self.c = User.objects.create_user("c", password="pw")

    def _vote(self, percent="50.00"):
        return _vote([(self.a, "A", 60), (self.b, "B", 30),
                      (self.c, "C", 10)], _profile(percent=percent))

    def test_a_running_race_cannot_be_finalized_early(self):
        question = self._vote()
        services.cast(question, self.b, _choice(question, "For"))  # 30 of 100

        self.assertFalse(services.outcome_fixed(question))
        with self.assertRaises(PermissionDenied):
            services.record_decision(question, decided_by=self.staff)

    def test_an_unassailable_lead_may_finalize_early(self):
        question = self._vote()
        services.cast(question, self.a, _choice(question, "For"))  # 60 of 100

        self.assertTrue(services.outcome_fixed(question))
        decision = services.record_decision(question, decided_by=self.staff)
        self.assertTrue(decision.adopted)

    def test_a_hopeless_position_may_also_finalize_early(self):
        question = self._vote(percent="75.00")
        services.cast(question, self.a, _choice(question, "Against"))  # 60

        # Even if both remaining vote for, 40% cannot exceed 75%.
        self.assertTrue(services.outcome_fixed(question))
        decision = services.record_decision(question, decided_by=self.staff)
        self.assertFalse(decision.adopted)

    def test_everybody_having_voted_is_fixed(self):
        question = self._vote()
        for user, label in ((self.a, "For"), (self.b, "For"),
                            (self.c, "Against")):
            services.cast(question, user, _choice(question, label))

        self.assertTrue(services.outcome_fixed(question))

    def test_a_vote_with_no_rule_is_never_early_finalizable(self):
        """Without a rule there is no outcome to fix — staff keep the old
        deadline-only authority, and nothing else changes."""
        question = _vote([(self.a, "A", 1)])

        self.assertFalse(services.outcome_fixed(question))
        # Still closable by staff, exactly as before stage 7.
        self.assertIsNotNone(
            services.record_decision(question, decided_by=self.staff))

    def test_after_the_deadline_the_constraint_does_not_apply(self):
        question = self._vote()
        services.cast(question, self.b, _choice(question, "For"))
        _expire(question)

        self.assertIsNotNone(services.record_decision(question))
