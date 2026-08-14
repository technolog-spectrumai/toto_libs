"""Stage 10: the room's entitlement — quorum bars and convening modes."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from . import services
from .electorate_models import (ConsensusProfile, ConveningMode, Presence,
                                QuorumRule)
from .models import Choice, Kind, Question

User = get_user_model()


def _rule(name="Half the room", mode=QuorumRule.Mode.PERCENT,
          threshold="50.00"):
    return QuorumRule.objects.create(
        name=name, mode=mode,
        threshold=Decimal(threshold) if threshold else None)


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


def _expire(question):
    Question.objects.filter(pk=question.pk).update(
        closes_at=timezone.now() - timedelta(minutes=1))
    question.refresh_from_db()


class QuorumBase(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("op", password="pw",
                                              is_staff=True)
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")

    def _open(self, rule=None, convening="", attendance_b=Presence.PRESENT):
        question = _vote()
        if rule is not None:
            services.snapshot_quorum(question, rule)
        if convening:
            question.convening_mode = convening
            question.save()
        services.freeze_roll(question, entries=[
            (self.a, "A", 60, Presence.PRESENT, ""),
            (self.b, "B", 40, attendance_b, ""),
        ])
        return question


class QuorumSnapshotTests(QuorumBase):
    def test_the_vote_snapshots_mode_name_and_number(self):
        rule = _rule()
        question = self._open(rule)

        self.assertEqual(question.quorum_mode, "percent")
        self.assertEqual(question.quorum_name, "Half the room")
        self.assertEqual(question.quorum_threshold, Decimal("50.00"))

    def test_retuning_the_rule_never_rewrites_a_past_vote(self):
        rule = _rule()
        question = self._open(rule)

        rule.threshold = Decimal("90.00")
        rule.name = "Renamed"
        rule.save()

        question.refresh_from_db()
        self.assertEqual(question.quorum_name, "Half the room")
        self.assertEqual(question.quorum_threshold, Decimal("50.00"))

    def test_the_quorum_locks_once_voting_starts(self):
        question = self._open(_rule())

        question.quorum_threshold = Decimal("1.00")
        with self.assertRaises(ValueError):
            question.save()

    def test_the_convening_mode_locks_too(self):
        question = self._open(convening=ConveningMode.FORMAL)

        question.convening_mode = ConveningMode.UNIVERSAL
        with self.assertRaises(ValueError):
            question.save()


class QuorumEvaluationTests(QuorumBase):
    def test_no_additional_quorum_is_always_met(self):
        question = self._open(_rule(mode=QuorumRule.Mode.NONE,
                                    threshold=None),
                              attendance_b=Presence.ABSENT)

        quorum = services.evaluate_quorum(
            question, services.procedure_of(question))

        self.assertTrue(quorum["met"])

    def test_percent_quorum_reads_the_snapshotted_attendance(self):
        met = self._open(_rule())                                  # 100%
        unmet = self._open(_rule(name="Second"),
                           attendance_b=Presence.ABSENT)           # 60%... 60>=50 met!

        self.assertTrue(services.evaluate_quorum(
            met, services.procedure_of(met))["met"])
        # 60% attendance clears a 50% bar — quorum is "at least".
        self.assertTrue(services.evaluate_quorum(
            unmet, services.procedure_of(unmet))["met"])

    def test_percent_quorum_fails_below_the_bar(self):
        question = self._open(_rule(threshold="75.00"),
                              attendance_b=Presence.ABSENT)   # 60% < 75%

        self.assertFalse(services.evaluate_quorum(
            question, services.procedure_of(question))["met"])

    def test_the_bar_is_met_at_the_number(self):
        """A quorum is 'at least', where a consensus threshold is 'strictly
        more' — the two comparisons differ on purpose."""
        question = self._open(_rule(threshold="60.00"),
                              attendance_b=Presence.ABSENT)   # exactly 60%

        self.assertTrue(services.evaluate_quorum(
            question, services.procedure_of(question))["met"])

    def test_absolute_quorum_counts_weight_not_percent(self):
        question = self._open(_rule(mode=QuorumRule.Mode.ABSOLUTE,
                                    threshold="70.00"),
                              attendance_b=Presence.ABSENT)   # 60 represented

        self.assertFalse(services.evaluate_quorum(
            question, services.procedure_of(question))["met"])

    def test_manual_quorum_is_met_by_confirmation_on_the_record(self):
        question = self._open(_rule(mode=QuorumRule.Mode.MANUAL,
                                    threshold=None))

        before = services.evaluate_quorum(
            question, services.procedure_of(question))
        self.assertFalse(before["met"])

        services.confirm_quorum(question, self.staff)

        after = services.evaluate_quorum(
            question, services.procedure_of(question))
        self.assertTrue(after["met"])
        self.assertEqual(after["confirmation"]["by"], "op")

    def test_a_vote_without_a_rule_evaluates_to_none(self):
        question = self._open()

        self.assertIsNone(services.evaluate_quorum(
            question, services.procedure_of(question)))


class QuorumDecisionTests(QuorumBase):
    def test_a_threshold_vote_without_quorum_cannot_adopt(self):
        profile = ConsensusProfile.objects.create(name="Half",
                                                  percent=Decimal("50.00"))
        question = _vote()
        services.snapshot_rule(question, profile)
        services.snapshot_quorum(question, _rule(threshold="90.00"))
        services.freeze_roll(question, entries=[
            (self.a, "A", 60, Presence.PRESENT, ""),
            (self.b, "B", 40, Presence.ABSENT, ""),
        ])
        services.cast(question, self.a, question.choices.get(label="For"))
        _expire(question)

        decision = services.record_decision(question)

        # The arithmetic stays honest — 100% of the decided weight was for —
        # and the verdict still fails on the missing room.
        self.assertFalse(decision.adopted)
        self.assertTrue(decision.content["consensus"]["quorum_blocked"])
        self.assertFalse(decision.content["quorum"]["met"])

    def test_the_payload_carries_quorum_and_convening(self):
        question = self._open(_rule())
        _expire(question)

        decision = services.record_decision(question)

        self.assertTrue(decision.content["quorum"]["met"])
        self.assertEqual(decision.content["convening"]["mode"], "formal")
        self.assertEqual(decision.content["convening"]["missing"], [])

    def test_quorum_is_inside_the_hashed_payload(self):
        from .models import Decision

        question = self._open(_rule())
        _expire(question)
        decision = services.record_decision(question)

        forged = dict(decision.content)
        forged["quorum"] = {**forged["quorum"], "met": False}
        Decision.objects.filter(pk=decision.pk).update(content=forged)

        self.assertFalse(Decision.verify_chain().ok)


class UniversalMeetingTests(QuorumBase):
    def _universal(self, attendance_b=Presence.PRESENT):
        return self._open(convening=ConveningMode.UNIVERSAL,
                          attendance_b=attendance_b)

    def _confirm_all(self, question):
        for aspect in ("full_representation", "no_objection_meeting",
                       "no_objection_agenda"):
            services.confirm_convening(question, aspect, self.staff)

    def test_recording_needs_all_three_confirmations(self):
        question = self._universal()
        services.confirm_convening(question, "full_representation",
                                   self.staff)
        _expire(question)

        with self.assertRaises(PermissionDenied):
            services.record_decision(question)

    def test_a_confirmed_universal_meeting_records(self):
        question = self._universal()
        self._confirm_all(question)
        _expire(question)

        decision = services.record_decision(question)

        convening = decision.content["convening"]
        self.assertEqual(convening["mode"], "universal")
        self.assertEqual(convening["missing"], [])
        self.assertEqual(
            convening["confirmations"]["no_objection_agenda"]["by"], "op")

    def test_confirmations_cannot_paper_over_absentees(self):
        """The three confirmations assert 100%; the snapshot must agree —
        a universal meeting with somebody absent is not one."""
        question = self._universal(attendance_b=Presence.ABSENT)
        self._confirm_all(question)
        _expire(question)

        with self.assertRaises(PermissionDenied):
            services.record_decision(question)

    def test_an_unknown_aspect_is_refused(self):
        question = self._universal()

        with self.assertRaises(ValueError):
            services.confirm_convening(question, "no_objection_snacks",
                                       self.staff)
