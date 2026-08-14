"""The voting core, and the two things built on it.

The old app had no tests at all, and its one real rule — one ballot per voter —
was defeated by the view that used ``update_or_create``. These pin the rules
that make a formal vote worth calling one.
"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .core import (AlreadyCast, Eligibility, NotEligible, NotOpen, OpenToAll,
                   Revisability, UnknownChoice, Visibility)
from . import services
from .models import (SCOPE_COMPANY, SCOPE_FORUM, SCOPE_GLOBAL, Ballot, Choice,
                     Kind, Question, Status)

User = get_user_model()


def _question(**kwargs):
    kwargs.setdefault("title", "A question")
    kwargs.setdefault("question_text", "Well?")
    question = Question.objects.create(**kwargs)
    for position, label in enumerate(["Yes", "No"]):
        Choice.objects.create(question=question, label=label, position=position)
    return question


class ScopeTests(TestCase):
    """Company A must never see Company B's votes."""

    def test_scopes_are_separate_sets(self):
        _question(title="A's budget", scope_type=SCOPE_COMPANY, scope_id="1")
        _question(title="B's budget", scope_type=SCOPE_COMPANY, scope_id="2")

        a = Question.objects.in_scope(SCOPE_COMPANY, "1")
        b = Question.objects.in_scope(SCOPE_COMPANY, "2")

        self.assertEqual([q.title for q in a], ["A's budget"])
        self.assertEqual([q.title for q in b], ["B's budget"])

    def test_a_scoped_question_is_not_in_the_global_list(self):
        """The standalone Polls pages must not leak a room's or a company's."""
        _question(title="Room poll", scope_type=SCOPE_FORUM, scope_id="7")

        self.assertEqual(Question.objects.in_scope(SCOPE_GLOBAL).count(), 0)

    def test_two_scopes_may_use_the_same_slug(self):
        """Globally unique slugs meant two companies could not both hold a
        vote of the same name — the cross-tenant collision scoping prevents."""
        first = _question(title="Budget 2027", scope_type=SCOPE_COMPANY, scope_id="1")
        second = _question(title="Budget 2027", scope_type=SCOPE_COMPANY, scope_id="2")

        self.assertEqual(first.slug, second.slug)

    def test_slugs_still_do_not_collide_inside_one_scope(self):
        first = _question(title="Budget", scope_type=SCOPE_COMPANY, scope_id="1")
        second = _question(title="Budget", scope_type=SCOPE_COMPANY, scope_id="1")

        self.assertNotEqual(first.slug, second.slug)


class OpenClosedTests(TestCase):
    def test_a_deadline_closes_it_with_nothing_running(self):
        """No sweep, no task, no cron — a deadline enforced by a background job
        does not apply when the worker is down."""
        past = _question(closes_at=timezone.now() - timezone.timedelta(hours=1))

        self.assertFalse(past.is_open)
        self.assertEqual(past.status, Status.OPEN)

    def test_no_deadline_means_open_until_somebody_closes_it(self):
        self.assertTrue(_question(closes_at=None).is_open)

    def test_closing_is_idempotent(self):
        question = _question()
        question.close()
        first = question.closed_at
        question.close()

        self.assertEqual(question.closed_at, first)


class CastTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("voter", password="pw")
        self.other = User.objects.create_user("other", password="pw")

    def test_a_ballot_is_recorded_with_its_weight(self):
        question = _question()
        ballot = services.cast(question, self.user, question.choices.first())

        self.assertEqual(ballot.weight, 1)
        self.assertEqual(Ballot.objects.count(), 1)

    def test_a_closed_question_refuses_with_a_sentence(self):
        question = _question(closes_at=timezone.now() - timezone.timedelta(minutes=1))

        with self.assertRaises(NotOpen) as caught:
            services.cast(question, self.user, question.choices.first())

        self.assertIn("closed", str(caught.exception).lower())

    def test_an_option_from_another_question_is_refused(self):
        question, stranger = _question(), _question(title="Other")

        with self.assertRaises(UnknownChoice):
            services.cast(question, self.user, stranger.choices.first())

    def test_a_poll_may_be_revised(self):
        question = _question(revisability=Revisability.OPEN)
        yes, no = question.choices.all()[0], question.choices.all()[1]

        services.cast(question, self.user, yes)
        ballot = services.cast(question, self.user, no)

        self.assertEqual(ballot.choice_id, no.pk)
        self.assertEqual(ballot.revisions, 1)
        self.assertIsNotNone(ballot.revised_at)
        self.assertEqual(Ballot.objects.count(), 1)

    def test_a_formal_ballot_cannot_be_changed(self):
        """The rule the old view defeated by using update_or_create."""
        question = _question(kind=Kind.VOTE, revisability=Revisability.FINAL)
        yes, no = question.choices.all()[0], question.choices.all()[1]
        services.cast(question, self.user, yes)

        with self.assertRaises(AlreadyCast):
            services.cast(question, self.user, no)

        self.assertEqual(Ballot.objects.get().choice_id, yes.pk)

    def test_recasting_the_same_choice_is_not_a_revision(self):
        question = _question(revisability=Revisability.OPEN)
        choice = question.choices.first()
        services.cast(question, self.user, choice)

        ballot = services.cast(question, self.user, choice)

        self.assertEqual(ballot.revisions, 0)
        self.assertIsNone(ballot.revised_at)

    def test_revising_does_not_refresh_the_weight(self):
        """A tally must not depend on when somebody last clicked."""
        class Shifting:
            def __init__(self): self.n = 5
            def standing(self, question, user):
                self.n += 5
                return Eligibility(True, weight=self.n)
            def size(self, question): return 0

        roll = Shifting()
        question = _question(revisability=Revisability.OPEN)
        yes, no = question.choices.all()[0], question.choices.all()[1]
        first = services.cast(question, self.user, yes, electorate=roll)
        self.assertEqual(first.weight, 10)

        again = services.cast(question, self.user, no, electorate=roll)

        self.assertEqual(again.weight, 10)

    def test_an_ineligible_voter_is_refused_with_the_reason(self):
        class Nobody:
            def standing(self, question, user):
                return Eligibility(False, reason="You hold no shares.")
            def size(self, question): return 0

        question = _question()

        with self.assertRaises(NotEligible) as caught:
            services.cast(question, self.user, question.choices.first(),
                          electorate=Nobody())

        self.assertIn("no shares", str(caught.exception))

    def test_an_anonymous_visitor_may_not_vote_by_default(self):
        from django.contrib.auth.models import AnonymousUser

        question = _question()
        verdict = OpenToAll().standing(question, AnonymousUser())

        self.assertFalse(verdict.allowed)


class TallyTests(TestCase):
    def setUp(self):
        self.users = [User.objects.create_user(f"u{i}", password="pw")
                      for i in range(3)]

    def test_every_option_appears_including_the_empty_ones(self):
        """A bar chart missing its empty bars misreports the shape."""
        question = _question()
        services.cast(question, self.users[0], question.choices.first())

        counted = services.tally(question)

        self.assertEqual(len(counted.results), 2)
        self.assertTrue(counted.results[1].is_empty)

    def test_weight_and_ballots_are_reported_separately(self):
        """In a weighted vote they answer different questions, and showing one
        is how "3 of 5 agreed" becomes "60% approved"."""
        class Weighted:
            def standing(self, question, user):
                return Eligibility(True, weight=10)
            def size(self, question): return 0

        question = _question()
        for user in self.users:
            services.cast(question, user, question.choices.first(),
                          electorate=Weighted())

        counted = services.tally(question)

        self.assertEqual(counted.total_ballots, 3)
        self.assertEqual(counted.total_weight, 30)

    def test_a_tie_has_no_winner(self):
        """Picking one of two equal options would invent a result."""
        question = _question()
        yes, no = question.choices.all()[0], question.choices.all()[1]
        services.cast(question, self.users[0], yes)
        services.cast(question, self.users[1], no)

        self.assertIsNone(services.tally(question).winner)

    def test_turnout_is_none_without_a_roll_to_count(self):
        """Zero would read as "nobody voted" rather than "no fixed roll"."""
        question = _question()
        services.cast(question, self.users[0], question.choices.first())

        self.assertIsNone(services.tally(question).turnout)

    def test_turnout_is_a_fraction_when_the_electorate_is_known(self):
        class Roll:
            def standing(self, question, user):
                return Eligibility(True, weight=1)
            def size(self, question): return 4

        question = _question()
        services.cast(question, self.users[0], question.choices.first(),
                      electorate=Roll())

        self.assertEqual(services.tally(question, electorate=Roll()).turnout, 0.25)


class ResultVisibilityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("reader", password="pw")

    def test_a_live_poll_shows_its_count_while_open(self):
        question = _question(visibility=Visibility.LIVE)

        self.assertTrue(services.may_see_results(question, self.user))

    def test_a_formal_vote_stays_sealed_until_it_closes(self):
        """A running tally in a formal decision is an instrument for changing it."""
        question = _question(kind=Kind.VOTE, visibility=Visibility.ON_CLOSE)

        self.assertFalse(services.may_see_results(question, self.user))

    def test_it_opens_once_the_deadline_passes(self):
        question = _question(kind=Kind.VOTE, visibility=Visibility.ON_CLOSE,
                             closes_at=timezone.now() - timezone.timedelta(minutes=1))

        self.assertTrue(services.may_see_results(question, self.user))

    def test_a_private_count_stays_inside_the_electorate(self):
        class Nobody:
            def standing(self, question, user):
                return Eligibility(False, reason="not yours")
            def size(self, question): return 0

        question = _question(kind=Kind.VOTE,
                             visibility=Visibility.ON_CLOSE_PRIVATE,
                             closes_at=timezone.now() - timezone.timedelta(minutes=1))

        self.assertFalse(services.may_see_results(question, self.user,
                                                  electorate=Nobody()))
