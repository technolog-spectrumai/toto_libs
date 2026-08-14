"""Stage 9: barred from one vote, still a member of the body."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import services
from .core import NotEligible
from .electorate_models import (ConsensusProfile, Presence, VoteExclusion,
                                VoteProcedure)
from .models import Choice, Decision, Kind, Question

User = get_user_model()


def _vote(**kwargs):
    kwargs.setdefault("title", "A resolution")
    kwargs.setdefault("question_text", "Well?")
    kwargs.setdefault("kind", Kind.VOTE)
    kwargs.setdefault("closes_at", timezone.now() + timedelta(days=1))
    question = Question.objects.create(**kwargs)
    Choice.objects.create(question=question, label="For", value=1, position=0)
    Choice.objects.create(question=question, label="Against", value=-1,
                          position=1)
    return question


class ExclusionBase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")
        self.c = User.objects.create_user("c", password="pw")
        self.question = _vote()
        services.freeze_roll(self.question, entries=[
            (self.a, "A", 60), (self.b, "B", 30), (self.c, "C", 10)])

    def _exclude(self, user, reason="Conflict of interest in this matter."):
        return services.exclude_voter(self.question, user=user, reason=reason,
                                      excluded_by=self.staff)

    def _expire(self):
        Question.objects.filter(pk=self.question.pk).update(
            closes_at=timezone.now() - timedelta(minutes=1))
        self.question.refresh_from_db()


class ExclusionRecordTests(ExclusionBase):
    def test_an_exclusion_records_who_why_by_whom_and_when(self):
        exclusion = self._exclude(self.a)

        self.assertEqual(exclusion.user, self.a)
        self.assertEqual(exclusion.label, "A")
        self.assertEqual(exclusion.reason,
                         "Conflict of interest in this matter.")
        self.assertEqual(exclusion.excluded_by, self.staff)
        self.assertIsNotNone(exclusion.created_at)

    def test_an_exclusion_without_a_reason_is_refused(self):
        """A silent disenfranchisement is the thing this model exists to
        prevent."""
        with self.assertRaises(ValueError):
            self._exclude(self.a, reason="   ")

    def test_excluding_somebody_off_the_register_is_refused(self):
        stranger = User.objects.create_user("stranger", password="pw")

        with self.assertRaises(ValueError):
            self._exclude(stranger)

    def test_the_electorate_membership_is_untouched(self):
        """Exclusion is a fact about this question, not about the body."""
        self._exclude(self.a)

        self.assertEqual(self.question.roll.count(), 3)
        self.assertTrue(self.question.roll.filter(user=self.a).exists())

    def test_an_exclusion_may_be_lifted_before_the_decision(self):
        exclusion = self._exclude(self.a)

        services.lift_exclusion(exclusion)

        self.assertFalse(services.is_excluded(self.question, self.a))
        self.assertTrue(services.standing(self.question, self.a).allowed)

    def test_exclusions_freeze_once_the_vote_is_decided(self):
        exclusion = self._exclude(self.a)
        self._expire()
        services.record_decision(self.question)

        with self.assertRaises(ValueError):
            services.lift_exclusion(exclusion)
        with self.assertRaises(ValueError):
            services.exclude_voter(self.question, user=self.b,
                                   reason="Too late.", excluded_by=self.staff)


class ExclusionEffectTests(ExclusionBase):
    def test_an_excluded_member_cannot_cast(self):
        self._exclude(self.a)

        verdict = services.standing(self.question, self.a)
        self.assertFalse(verdict.allowed)
        self.assertIn("excluded", verdict.reason.lower())

        with self.assertRaises(NotEligible):
            services.cast(self.question, self.a,
                          self.question.choices.first())

    def test_everybody_else_still_votes(self):
        self._exclude(self.a)

        ballot = services.cast(self.question, self.b,
                               self.question.choices.get(label="For"))

        self.assertEqual(ballot.weight, 30)

    def test_excluded_weight_leaves_the_eligible_weight(self):
        self._exclude(self.a)   # 60 of 100

        procedure = services.procedure_of(self.question)
        self.assertEqual(procedure.electorate_weight, 100)
        self.assertEqual(procedure.represented_weight, 100)
        self.assertEqual(procedure.eligible_weight, 40)
        self.assertEqual(procedure.excluded_weight, 60)
        self.assertEqual(procedure.members_excluded, 1)
        # Attendance is about the room, and the room is unchanged.
        self.assertEqual(procedure.attendance_percent, 100.0)

    def test_lifting_restores_the_eligible_weight(self):
        exclusion = self._exclude(self.a)

        services.lift_exclusion(exclusion)

        procedure = services.procedure_of(self.question)
        self.assertEqual(procedure.eligible_weight, 100)
        self.assertEqual(procedure.excluded_weight, 0)

    def test_barred_weight_cannot_hold_an_outcome_open(self):
        """A vote whose only holdouts are excluded is settled.

        Built fresh because the rule is snapshotted BEFORE the register is
        frozen — after that the instrument is locked, which is stage 6's
        rule doing its job.
        """
        profile = ConsensusProfile.objects.create(name="Half", percent=50)
        question = _vote(title="Barred holdout")
        services.snapshot_rule(question, profile)
        services.freeze_roll(question, entries=[
            (self.a, "A", 60), (self.b, "B", 30), (self.c, "C", 10)])
        services.exclude_voter(question, user=self.c, reason="Conflicted.",
                               excluded_by=self.staff)
        services.cast(question, self.a,
                      question.choices.get(label="For"))       # 60
        services.cast(question, self.b,
                      question.choices.get(label="Against"))   # 30

        self.assertTrue(services.outcome_fixed(question))


class ExclusionPayloadTests(ExclusionBase):
    def test_the_decision_carries_every_exclusion(self):
        self._exclude(self.a)
        self._expire()

        decision = services.record_decision(self.question)

        exclusions = decision.content["exclusions"]
        self.assertEqual(len(exclusions), 1)
        self.assertEqual(exclusions[0]["label"], "A")
        self.assertEqual(exclusions[0]["reason"],
                         "Conflict of interest in this matter.")
        self.assertEqual(exclusions[0]["excluded_by"], "op")
        self.assertIn("created_at", exclusions[0])

    def test_the_procedure_in_the_payload_holds_the_barred_weight_out(self):
        self._exclude(self.a)
        self._expire()

        procedure = services.record_decision(
            self.question).content["procedure"]

        self.assertEqual(procedure["eligible_weight"], 40)
        self.assertEqual(procedure["excluded_weight"], 60)
        self.assertEqual(procedure["members_excluded"], 1)

    def test_exclusions_are_inside_the_hashed_payload(self):
        from .models import compute_hash

        self._exclude(self.a)
        self._expire()
        decision = services.record_decision(self.question)

        self.assertEqual(decision.content_hash,
                         compute_hash(decision.content, decision.prev_hash))
        forged = dict(decision.content)
        forged["exclusions"] = []
        Decision.objects.filter(pk=decision.pk).update(content=forged)

        self.assertFalse(Decision.verify_chain().ok)

    def test_a_vote_with_no_exclusions_records_an_empty_list(self):
        self._expire()

        decision = services.record_decision(self.question)

        self.assertEqual(decision.content["exclusions"], [])
