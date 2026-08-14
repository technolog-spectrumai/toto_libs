"""The registry: keys to policy, failing closed where it matters."""

from django.contrib.auth import get_user_model
from django.test import TestCase

from . import electorates, services
from .core import Eligibility, NotEligible, OpenToAll
from .models import Choice, Kind, Question

User = get_user_model()


def _question(**kwargs):
    kwargs.setdefault("title", "A question")
    kwargs.setdefault("question_text", "Well?")
    question = Question.objects.create(**kwargs)
    for position, label in enumerate(["Yes", "No"]):
        Choice.objects.create(question=question, label=label,
                              position=position)
    return question


class RegistryTests(TestCase):
    def tearDown(self):
        electorates._REGISTRY.pop("test-roll", None)
        electorates._SCOPE_DEFAULTS.pop("test.scope", None)

    def test_register_and_resolve_round_trip(self):
        marker = object()
        electorates.register("test-roll", lambda question: marker)
        question = _question(metadata={"electorate": "test-roll"})

        self.assertIs(electorates.resolve(question), marker)

    def test_a_taken_key_refuses_a_second_factory(self):
        electorates.register("test-roll", lambda question: None)

        with self.assertRaises(electorates.DuplicateElectorate):
            electorates.register("test-roll", lambda question: "other")

    def test_reregistering_the_same_factory_is_idempotent(self):
        factory = lambda question: None  # noqa: E731
        electorates.register("test-roll", factory)
        electorates.register("test-roll", factory)  # no raise

    def test_an_unknown_key_fails_closed_for_a_formal_vote(self):
        """A governance instrument whose roll cannot be resolved must refuse
        ballots, not silently become a free-for-all."""
        vote = _question(kind=Kind.VOTE, metadata={"electorate": "no-such"})
        user = User.objects.create_user("v", password="pw")

        verdict = services.standing(vote, user)
        self.assertFalse(verdict.allowed)

        with self.assertRaises(NotEligible):
            services.cast(vote, user, vote.choices.first())

    def test_an_unknown_key_falls_back_to_open_for_a_poll(self):
        poll = _question(kind=Kind.POLL, metadata={"electorate": "no-such"})

        self.assertIsInstance(electorates.resolve(poll), OpenToAll)

    def test_scope_default_dispatch(self):
        """A scoped question with no stored key resolves its scope's default."""
        marker = object()
        electorates.register("test-roll", lambda question: marker)
        electorates.register_scope_default("test.scope", "test-roll")
        question = _question(scope_type="test.scope", scope_id="9")

        self.assertIs(electorates.resolve(question), marker)

    def test_a_factory_that_raises_fails_closed_for_a_vote(self):
        def broken(question):
            raise RuntimeError("boom")
        electorates.register("test-roll", broken)
        vote = _question(kind=Kind.VOTE, metadata={"electorate": "test-roll"})

        roll = electorates.resolve(vote)
        self.assertIsInstance(roll, electorates.NullElectorate)


class BuiltinElectorateTests(TestCase):
    def test_all_admits_active_users_and_counts_them(self):
        active = User.objects.create_user("active", password="pw")
        inactive = User.objects.create_user("inactive", password="pw",
                                            is_active=False)
        vote = _question(kind=Kind.VOTE)  # global default: "all"

        roll = electorates.resolve(vote)
        self.assertTrue(roll.standing(vote, active).allowed)
        self.assertFalse(roll.standing(vote, inactive).allowed)
        # A real denominator: the turnout is a fraction, not None.
        services.cast(vote, active, vote.choices.first())
        self.assertEqual(services.tally(vote).electorate, 1)
        self.assertEqual(services.tally(vote).turnout, 1.0)

    def test_staff_admits_only_operators(self):
        staff = User.objects.create_user("op", password="pw", is_staff=True)
        civilian = User.objects.create_user("civ", password="pw")
        vote = _question(kind=Kind.VOTE, metadata={"electorate": "staff"})

        roll = electorates.resolve(vote)
        self.assertTrue(roll.standing(vote, staff).allowed)
        verdict = roll.standing(vote, civilian)
        self.assertFalse(verdict.allowed)
        self.assertTrue(verdict.reason)
        self.assertEqual(roll.size(vote), 1)


class ServicesResolutionTests(TestCase):
    def test_the_stub_is_dead(self):
        """services with no electorate= now flow through the registry."""
        civilian = User.objects.create_user("civ", password="pw")
        vote = _question(kind=Kind.VOTE, metadata={"electorate": "staff"})

        self.assertFalse(services.standing(vote, civilian).allowed)

    def test_an_explicit_electorate_still_overrides(self):
        class Everyone:
            def standing(self, question, user):
                return Eligibility(True, weight=7)

            def size(self, question):
                return 1

        civilian = User.objects.create_user("civ", password="pw")
        vote = _question(kind=Kind.VOTE, metadata={"electorate": "staff"})

        ballot = services.cast(vote, civilian, vote.choices.first(),
                               electorate=Everyone())
        self.assertEqual(ballot.weight, 7)
