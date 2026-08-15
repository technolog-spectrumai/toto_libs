"""Stage 5: electorates as configurable data, and the register they freeze."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .electorate_models import Electorate, ElectorateMember, RollEntry
from .electorates import SnapshotElectorate
from .models import SCOPE_FORUM, Choice, Kind, Question

User = get_user_model()


def _person(user):
    """The Person behind a login. Membership is a person, not an account —
    so the tests make one, the way the platform does."""
    from toto.people.models import Person

    person, _created = Person.objects.get_or_create(
        user=user, defaults={"display_name": user.get_username()})
    return person


def _platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _roll(name="Board", kind=Electorate.Kind.EQUAL, **kwargs):
    return Electorate.objects.create(name=name, kind=kind, **kwargs)


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


class ElectorateDataTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")

    def test_an_equal_roll_gives_everybody_one_voice(self):
        roll = _roll()
        ElectorateMember.objects.create(
            electorate=roll, person=_person(self.a))
        ElectorateMember.objects.create(
            electorate=roll, person=_person(self.b))

        self.assertEqual(roll.total_weight(), 2)
        self.assertEqual({row["percent"] for row in roll.power_table()},
                         {50.0})

    def test_an_unequal_roll_reports_real_percentages(self):
        roll = _roll(kind=Electorate.Kind.WEIGHTED)
        ElectorateMember.objects.create(
            electorate=roll, person=_person(self.a), weight=75)
        ElectorateMember.objects.create(
            electorate=roll, person=_person(self.b), weight=25)

        table = {row["member"].user: row["percent"]
                 for row in roll.power_table()}

        self.assertEqual(roll.total_weight(), 100)
        self.assertEqual(table[self.a], 75.0)
        self.assertEqual(table[self.b], 25.0)

    def test_a_member_appears_once_per_roll(self):
        from django.db import IntegrityError

        roll = _roll()
        ElectorateMember.objects.create(
            electorate=roll, person=_person(self.a))

        with self.assertRaises(IntegrityError):
            ElectorateMember.objects.create(
            electorate=roll, person=_person(self.a))

    def test_rolls_are_scope_isolated(self):
        _roll(name="Global roll")
        _roll(name="Room roll", scope_type=SCOPE_FORUM, scope_id="7")

        self.assertEqual(
            [e.name for e in Electorate.objects.in_scope("")],
            ["Global roll"])


class FreezeRollTests(TestCase):
    def setUp(self):
        self.a = User.objects.create_user("a", password="pw")
        self.b = User.objects.create_user("b", password="pw")
        self.roll = _roll(kind=Electorate.Kind.WEIGHTED)
        ElectorateMember.objects.create(
            electorate=self.roll, person=_person(self.a), weight=60)
        ElectorateMember.objects.create(
            electorate=self.roll, person=_person(self.b), weight=40)

    def test_opening_copies_membership_and_weights(self):
        question = _vote()

        services.freeze_roll(question, electorate=self.roll)

        entries = {e.user: e.weight for e in question.roll.all()}
        self.assertEqual(entries, {self.a: 60, self.b: 40})
        self.assertEqual(question.electorate, self.roll)
        self.assertIn("roll_frozen_at", question.metadata)

    def test_later_electorate_edits_do_not_touch_a_frozen_vote(self):
        """The record date, generically — stage 5's whole point."""
        question = _vote()
        services.freeze_roll(question, electorate=self.roll)

        ElectorateMember.objects.filter(
            electorate=self.roll, person=_person(self.a)).update(weight=1)
        late = User.objects.create_user("late", password="pw")
        ElectorateMember.objects.create(
            electorate=self.roll, person=_person(late), weight=99)

        self.assertEqual(question.roll.get(user=self.a).weight, 60)
        self.assertEqual(question.roll.count(), 2)
        self.assertFalse(services.standing(question, late).allowed)

    def test_the_register_answers_standing_and_size(self):
        question = _vote()
        services.freeze_roll(question, electorate=self.roll)

        roll = services.electorate_for(question)

        self.assertIsInstance(roll, SnapshotElectorate)
        self.assertEqual(roll.standing(question, self.a).weight, 60)
        self.assertEqual(roll.size(question), 2)

    def test_a_register_cannot_be_refrozen_or_edited(self):
        question = _vote()
        services.freeze_roll(question, electorate=self.roll)

        with self.assertRaises(ValueError):
            services.freeze_roll(question, electorate=self.roll)

        entry = question.roll.first()
        entry.weight = 999
        with self.assertRaises(ValueError):
            entry.save()

    def test_an_empty_roll_is_refused(self):
        question = _vote()

        with self.assertRaises(ValueError):
            services.freeze_roll(question, electorate=_roll(name="Hollow"))

    def test_entries_may_be_supplied_by_a_host(self):
        """Business Center computes its own from its registers; a person
        with no login is ON the roll and cannot cast."""
        question = _vote()

        services.freeze_roll(question, entries=[
            (self.a, "Ada", 10), (None, "Ghost", 5)])

        self.assertEqual(question.roll.count(), 2)
        self.assertEqual(services.tally(question).electorate, 2)
        self.assertFalse(services.standing(question, self.b).allowed)


class ElectoratePageTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("u", password="pw")
        self.client.force_login(self.user)

    def test_the_pages_render_members_weights_and_percentages(self):
        roll = _roll(name="Assembly", kind=Electorate.Kind.WEIGHTED)
        ElectorateMember.objects.create(
            electorate=roll, person=_person(self.user), weight=30)

        listing = self.client.get(reverse("polls:electorate_list"))
        detail = self.client.get(
            reverse("polls:electorate_detail", args=[roll.slug]))

        self.assertContains(listing, "Assembly")
        self.assertContains(detail, "100.0%")
        # Generic vocabulary: this is voting power, never "shares".
        self.assertNotContains(detail, "shares")

    def test_a_scoped_roll_never_lists_globally(self):
        _roll(name="Room roll", scope_type=SCOPE_FORUM, scope_id="7")

        response = self.client.get(reverse("polls:electorate_list"))

        self.assertNotContains(response, "Room roll")
