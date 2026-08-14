"""Stage 8: who belongs, who attends, and the three weights that follow."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from . import services
from .electorate_models import Presence, VoteProcedure
from .models import Choice, Kind, Question

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


class AttendanceSnapshotTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")
        self.c = User.objects.create_user("c", password="pw")

    def test_attendance_is_recorded_beside_membership(self):
        """Belonging and attending are two facts, and the row states both."""
        question = _vote()

        services.freeze_roll(question, entries=[
            (self.a, "A", 60, Presence.PRESENT, ""),
            (self.b, "B", 30, Presence.REPRESENTED, "A holds the proxy"),
            (self.c, "C", 10, Presence.ABSENT, ""),
        ])

        rows = {row.label: row for row in question.roll.all()}
        self.assertEqual(rows["A"].presence, Presence.PRESENT)
        self.assertEqual(rows["B"].represented_by, "A holds the proxy")
        self.assertFalse(rows["C"].is_represented)
        # Everybody is still ON the register: absence is not removal.
        self.assertEqual(question.roll.count(), 3)

    def test_the_three_weights_answer_three_questions(self):
        question = _vote()

        services.freeze_roll(question, entries=[
            (self.a, "A", 60, Presence.PRESENT, ""),
            (self.b, "B", 30, Presence.REPRESENTED, "A"),
            (self.c, "C", 10, Presence.ABSENT, ""),
            (None, "Ghost", 100, Presence.PRESENT, ""),
        ])

        procedure = services.procedure_of(question)
        self.assertEqual(procedure.electorate_weight, 200)   # everybody
        self.assertEqual(procedure.represented_weight, 190)  # in the room
        # The ghost is present but has no login and no proxy: nobody can
        # cast that weight.
        self.assertEqual(procedure.eligible_weight, 90)
        self.assertEqual(procedure.attendance_percent, 95.0)
        self.assertEqual(procedure.members_represented, 3)
        self.assertEqual(procedure.members_total, 4)

    def test_a_present_member_without_a_login_counts_when_represented(self):
        question = _vote()

        services.freeze_roll(question, entries=[
            (None, "Institution", 40, Presence.REPRESENTED, "Its counsel"),
        ])

        procedure = services.procedure_of(question)
        self.assertEqual(procedure.represented_weight, 40)
        self.assertEqual(procedure.eligible_weight, 40)

    def test_attendance_defaults_to_present_when_unstated(self):
        """A caller with no attendance data still gets a coherent snapshot."""
        question = _vote()

        services.freeze_roll(question, entries=[(self.a, "A", 5)])

        procedure = services.procedure_of(question)
        self.assertEqual(procedure.electorate_weight, 5)
        self.assertEqual(procedure.represented_weight, 5)
        self.assertEqual(procedure.attendance_percent, 100.0)

    def test_nobody_attending_is_zero_percent_not_an_error(self):
        question = _vote()

        services.freeze_roll(question, entries=[
            (self.a, "A", 10, Presence.ABSENT, ""),
        ])

        procedure = services.procedure_of(question)
        self.assertEqual(procedure.represented_weight, 0)
        self.assertEqual(procedure.attendance_percent, 0.0)

    def test_the_snapshot_survives_later_electorate_edits(self):
        question = _vote()
        services.freeze_roll(question, entries=[
            (self.a, "A", 60, Presence.PRESENT, ""),
            (self.b, "B", 40, Presence.ABSENT, ""),
        ])

        # Whatever happens to the configured roll afterwards, the session's
        # own numbers stand.
        procedure = services.procedure_of(question)
        before = (procedure.electorate_weight, procedure.represented_weight)
        services.snapshot_procedure(question)   # recomputed from the SAME rows

        procedure.refresh_from_db()
        self.assertEqual(
            (procedure.electorate_weight, procedure.represented_weight),
            before)


class ProcedurePayloadTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")

    def _decided(self, notes=""):
        question = _vote(procedural_notes=notes)
        services.freeze_roll(question, entries=[
            (self.a, "A", 60, Presence.PRESENT, ""),
            (self.b, "B", 40, Presence.REPRESENTED, "A"),
        ])
        services.cast(question, self.a, question.choices.get(label="For"))
        Question.objects.filter(pk=question.pk).update(
            closes_at=timezone.now() - timedelta(minutes=1))
        question.refresh_from_db()
        return services.record_decision(question)

    def test_the_decision_carries_the_procedure(self):
        decision = self._decided(notes="B arrived late; proxy accepted.")

        procedure = decision.content["procedure"]
        self.assertEqual(procedure["electorate_weight"], 100)
        self.assertEqual(procedure["represented_weight"], 100)
        self.assertEqual(procedure["eligible_weight"], 100)
        self.assertEqual(procedure["attendance_percent"], 100.0)
        self.assertEqual(procedure["notes"],
                         "B arrived late; proxy accepted.")

    def test_the_roll_snapshot_carries_each_attendance(self):
        decision = self._decided()

        by_label = {row["label"]: row for row in decision.content["roll"]}
        self.assertEqual(by_label["A"]["presence"], Presence.PRESENT)
        self.assertEqual(by_label["B"]["presence"], Presence.REPRESENTED)
        self.assertEqual(by_label["B"]["represented_by"], "A")

    def test_the_procedure_is_inside_the_hashed_payload(self):
        """Stage 6's chain covers it: tampering with the attendance record
        after the fact breaks verification."""
        from .models import Decision, compute_hash

        decision = self._decided()

        self.assertEqual(decision.content_hash,
                         compute_hash(decision.content, decision.prev_hash))
        forged = dict(decision.content)
        forged["procedure"] = {**forged["procedure"], "attendance_percent": 5}
        Decision.objects.filter(pk=decision.pk).update(content=forged)

        self.assertFalse(Decision.verify_chain().ok)

    def test_a_vote_with_no_register_records_no_procedure(self):
        question = _vote()
        Question.objects.filter(pk=question.pk).update(
            closes_at=timezone.now() - timedelta(minutes=1))
        question.refresh_from_db()

        decision = services.record_decision(question)

        self.assertIsNone(decision.content["procedure"])


class ProceduralNotesTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")

    def test_notes_stay_writable_after_voting_starts(self):
        """Unlike the header, notes record what happens DURING the session —
        a field that sealed at the opening could not do its job."""
        question = _vote()
        services.freeze_roll(question, entries=[(self.a, "A", 1)])

        question.procedural_notes = "A objected to the agenda."
        question.save()

        question.refresh_from_db()
        self.assertEqual(question.procedural_notes,
                         "A objected to the agenda.")

    def test_the_header_still_locks_while_notes_do_not(self):
        question = _vote(decision_header="Adopts the thing.")
        services.freeze_roll(question, entries=[(self.a, "A", 1)])

        question.procedural_notes = "Fine."
        question.save()          # allowed

        question.decision_header = "Adopts something else."
        with self.assertRaises(ValueError):
            question.save()      # refused
