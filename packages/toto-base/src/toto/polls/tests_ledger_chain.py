"""Stage 6: Vote → Decision → Ledger, hashed into an append-only chain."""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import SCOPE_GLOBAL, Choice, Decision, Kind, Question, compute_hash

User = get_user_model()


def _platform():
    from toto.core.models import Platform

    Platform.objects.get_or_create(
        site_name="Test",
        defaults={"author": "t", "publication_year": 2026, "active": True})


def _decided(title="A vote", *, scope_type="", scope_id="", header="",
             comment="", user=None):
    question = Question.objects.create(
        kind=Kind.VOTE, title=title, question_text="Well?",
        scope_type=scope_type, scope_id=scope_id,
        decision_header=header, decision_comment=comment,
        closes_at=timezone.now() + timedelta(hours=1))
    Choice.objects.create(question=question, label="For", value=1)
    Choice.objects.create(question=question, label="Against", value=-1)
    Question.objects.filter(pk=question.pk).update(
        closes_at=timezone.now() - timedelta(minutes=1))
    question.refresh_from_db()
    return services.record_decision(question, decided_by=user)


class ChainTests(TestCase):
    def test_the_first_decision_starts_the_chain(self):
        decision = _decided()

        self.assertEqual(decision.prev_hash, "")
        self.assertEqual(decision.content_hash,
                         compute_hash(decision.content, ""))

    def test_each_decision_links_the_one_before(self):
        first = _decided("One")
        second = _decided("Two")

        self.assertEqual(second.prev_hash, first.content_hash)
        self.assertTrue(Decision.verify_chain().ok)

    def test_scopes_carry_independent_chains(self):
        globalish = _decided("Global")
        scoped = _decided("Company", scope_type="forum.channel",
                          scope_id="1")

        self.assertEqual(scoped.prev_hash, "")
        self.assertNotEqual(scoped.content_hash, globalish.content_hash)
        self.assertTrue(Decision.verify_chain().ok)
        self.assertTrue(
            Decision.verify_chain("forum.channel", "1").ok)

    def test_a_tampered_payload_is_detected_and_named(self):
        _decided("One")
        victim = _decided("Two")
        _decided("Three")

        Decision.objects.filter(pk=victim.pk).update(
            content={"forged": True})

        result = Decision.verify_chain()
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_pk, victim.pk)
        self.assertEqual(result.checked, 1)   # the one before it verified

    def test_a_tampered_hash_is_detected(self):
        _decided("One")
        victim = _decided("Two")

        Decision.objects.filter(pk=victim.pk).update(
            content_hash="0" * 64)

        self.assertEqual(Decision.verify_chain().first_bad_pk, victim.pk)

    def test_a_decision_refuses_edits_and_deletes(self):
        decision = _decided()

        with self.assertRaises(ValueError):
            decision.save()
        with self.assertRaises(ValueError):
            decision.delete()

    def test_the_hash_is_deterministic_over_key_order(self):
        first = compute_hash({"a": 1, "b": 2}, "seed")
        second = compute_hash({"b": 2, "a": 1}, "seed")

        self.assertEqual(first, second)


class DecisionPayloadTests(TestCase):
    def test_the_snapshot_carries_the_whole_instrument(self):
        user = User.objects.create_user("op", password="pw", is_staff=True)
        decision = _decided(header="Adopts the new charter.",
                            comment="Context for the record.", user=user)

        content = decision.content
        for key in ("question", "proposer", "window", "electorate", "roll",
                    "outcome", "tally", "totals", "ballots", "consensus",
                    "decision_header", "decision_comment"):
            self.assertIn(key, content)
        self.assertEqual(content["decision_header"],
                         "Adopts the new charter.")
        self.assertEqual(content["decision_comment"],
                         "Context for the record.")
        self.assertEqual(content["question"]["scope_type"], SCOPE_GLOBAL)

    def test_exactly_one_decision_per_vote(self):
        decision = _decided()
        again = services.record_decision(decision.question)

        self.assertEqual(decision.pk, again.pk)
        self.assertEqual(Decision.objects.count(), 1)


class HeaderLockTests(TestCase):
    def test_the_header_and_comment_lock_once_voting_starts(self):
        user = User.objects.create_user("v", password="pw")
        question = Question.objects.create(
            kind=Kind.VOTE, title="A vote", question_text="Well?",
            decision_header="Original.", decision_comment="Context.",
            closes_at=timezone.now() + timedelta(days=1))
        services.freeze_roll(question, entries=[(user, "V", 1)])

        question.decision_header = "Rewritten."
        with self.assertRaises(ValueError):
            question.save()

    def test_a_vote_without_a_register_may_still_be_edited(self):
        question = Question.objects.create(
            kind=Kind.VOTE, title="A vote", question_text="Well?",
            opens_at=timezone.now() + timedelta(hours=2),
            decision_header="Original.",
            closes_at=timezone.now() + timedelta(days=1))

        question.decision_header = "Rewritten."
        question.save()

        question.refresh_from_db()
        self.assertEqual(question.decision_header, "Rewritten.")

    def test_closing_a_started_vote_is_not_an_edit(self):
        user = User.objects.create_user("v2", password="pw")
        question = Question.objects.create(
            kind=Kind.VOTE, title="A vote", question_text="Well?",
            decision_header="Original.",
            closes_at=timezone.now() + timedelta(days=1))
        services.freeze_roll(question, entries=[(user, "V", 1)])

        question.close()  # must not raise

        question.refresh_from_db()
        self.assertEqual(question.status, "closed")


class LedgerVerifyPageTests(TestCase):
    def setUp(self):
        _platform()
        self.user = User.objects.create_user("u", password="pw")
        self.client.force_login(self.user)

    def test_the_page_reports_an_intact_chain(self):
        _decided("One")

        response = self.client.get(reverse("polls:ledger_verify"))

        self.assertContains(response, "Chain intact")

    def test_the_page_names_the_first_broken_entry(self):
        _decided("One")
        victim = _decided("Two")
        Decision.objects.filter(pk=victim.pk).update(content={"forged": True})

        response = self.client.get(reverse("polls:ledger_verify"))

        self.assertContains(response, "Chain BROKEN")
        self.assertContains(response, f"#{victim.pk}")
