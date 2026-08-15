"""Snapshots: a ledger STATE, per electorate, verifiable without the row.

The claims under test are the ones the feature is for: identity is the state
(not the person, not the moment), the timestamp is the ledger's own, a QR
verifies against the decisions themselves so deleting snapshots changes
nothing, and electorates are isolated from each other.

Run only where a gate stanza names this module.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from toto.people.models import Person

from . import services, snapshots
from .electorate_models import Electorate, ElectorateMember
from .models import Choice, Decision, Kind, Question
from .tests_ledger_chain import _platform

User = get_user_model()


def _member(name, roll, weight=1):
    user = User.objects.create_user(name, password="pw")
    person = Person.objects.create(user=user, display_name=name.title())
    ElectorateMember.objects.create(electorate=roll, person=person,
                                    weight=weight)
    return user, person


def _decide(roll, title):
    question = Question.objects.create(
        kind=Kind.VOTE, title=title, question_text="Well?", electorate=roll,
        closes_at=timezone.now() + timedelta(hours=1))
    Choice.objects.create(question=question, label="For", value=1)
    Choice.objects.create(question=question, label="Against", value=-1)
    Question.objects.filter(pk=question.pk).update(
        closes_at=timezone.now() - timedelta(minutes=1))
    question.refresh_from_db()
    return services.record_decision(question)


class LedgerBelongsToAnElectorateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.acme = Electorate.objects.create(
            name="Acme board", scope_type="socialhub.community", scope_id="1")
        cls.other = Electorate.objects.create(
            name="Other co", scope_type="socialhub.community", scope_id="2")
        cls.alice, cls.alice_person = _member("alice", cls.acme, weight=5)
        cls.bob, cls.bob_person = _member("bob", cls.other)

    def test_a_decision_is_stamped_with_its_roll(self):
        decision = _decide(self.acme, "Budget")
        self.assertEqual(decision.electorate_id, self.acme.pk)

    def test_one_rolls_ledger_never_contains_anothers(self):
        _decide(self.acme, "Ours")
        _decide(self.other, "Theirs")
        titles = list(snapshots.ledger_of(self.acme)
                      .values_list("title", flat=True))
        self.assertEqual(titles, ["Ours"])

    def test_a_person_sees_every_roll_they_sit_on_across_scopes(self):
        # The bug this fixes: the pages asked for scope_type="" while every
        # real electorate belongs to a company or community, so members saw
        # nothing while the admin showed their membership.
        third = Electorate.objects.create(
            name="Third body", scope_type="socialhub.community", scope_id="9")
        ElectorateMember.objects.create(electorate=third,
                                        person=self.alice_person)
        mine = set(Electorate.objects.for_person(self.alice_person)
                   .values_list("name", flat=True))
        self.assertEqual(mine, {"Acme board", "Third body"})
        self.assertNotIn("Other co", mine)


class SnapshotIsAStateTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roll = Electorate.objects.create(name="Board")
        cls.alice, cls.alice_person = _member("alice", cls.roll)
        cls.bob, cls.bob_person = _member("bob", cls.roll)

    def test_the_same_ledger_state_is_the_same_snapshot(self):
        _decide(self.roll, "One")
        first, created_first = snapshots.snapshot_current(self.roll,
                                                          by=self.alice)
        second, created_second = snapshots.snapshot_current(self.roll,
                                                            by=self.bob)
        self.assertTrue(created_first)
        self.assertFalse(created_second)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(snapshots.LedgerSnapshot.objects.count(), 1)

    def test_the_timestamp_is_the_ledgers_not_the_clicks(self):
        decision = _decide(self.roll, "One")
        snapshot, _created = snapshots.snapshot_current(self.roll)
        self.assertEqual(snapshot.ledger_modified_at, decision.decided_at)
        self.assertNotEqual(snapshot.ledger_modified_at,
                            snapshot.first_seen_at)

    def test_a_new_decision_is_a_new_state(self):
        _decide(self.roll, "One")
        first, _ = snapshots.snapshot_current(self.roll)
        _decide(self.roll, "Two")
        second, created = snapshots.snapshot_current(self.roll)
        self.assertTrue(created)
        self.assertNotEqual(second.pk, first.pk)
        self.assertEqual((first.entry_count, second.entry_count), (1, 2))

    def test_an_empty_ledger_has_no_state_to_snapshot(self):
        snapshot, created = snapshots.snapshot_current(self.roll)
        self.assertIsNone(snapshot)
        self.assertFalse(created)


class VerificationNeedsNoStoredRowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.roll = Electorate.objects.create(name="Board")
        cls.alice, cls.alice_person = _member("alice", cls.roll)

    def test_match_and_then_differs_when_the_ledger_grows(self):
        _decide(self.roll, "One")
        snapshot, _ = snapshots.snapshot_current(self.roll)
        self.assertEqual(
            snapshots.verify_payload(snapshot.payload)["verdict"], "MATCH")
        _decide(self.roll, "Two")
        self.assertEqual(
            snapshots.verify_payload(snapshot.payload)["verdict"], "DIFFERS")

    def test_deleting_the_snapshot_touches_neither_ledger_nor_qr(self):
        decision = _decide(self.roll, "One")
        snapshot, _ = snapshots.snapshot_current(self.roll)
        payload = snapshot.payload
        snapshot.delete()

        self.assertTrue(Decision.objects.filter(pk=decision.pk).exists())
        self.assertEqual(snapshots.LedgerSnapshot.objects.count(), 0)
        # The whole point: evidence does not live in this table.
        self.assertEqual(snapshots.verify_payload(payload)["verdict"], "MATCH")

    def test_a_display_column_edit_is_caught_though_the_chain_passes(self):
        decision = _decide(self.roll, "One")
        _decide(self.roll, "Two")
        snapshot, _ = snapshots.snapshot_current(self.roll)

        Decision.objects.filter(pk=decision.pk).update(winner_label="FORGED")

        # The chain hashes only `content`, so it still verifies …
        self.assertTrue(snapshots.verify_chain(self.roll).ok)
        # … and the snapshot catches what the chain cannot see.
        self.assertEqual(
            snapshots.verify_payload(snapshot.payload)["verdict"], "DIFFERS")

    def test_garbage_and_foreign_payloads_are_refused(self):
        from . import checkpoint

        with self.assertRaises(checkpoint.PayloadError):
            snapshots.verify_payload("not a checkpoint")
        # A global-scope checkpoint is not an electorate snapshot.
        payload = checkpoint.build_payload(
            scope_type="", scope_id="", entry_count=1, head_hex="abc",
            taken_at=timezone.now())
        with self.assertRaises(checkpoint.PayloadError):
            snapshots.verify_payload(payload)


class SnapshotPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.roll = Electorate.objects.create(name="Board")
        cls.other = Electorate.objects.create(name="Not mine")
        cls.alice, cls.alice_person = _member("alice", cls.roll)
        cls.stranger = User.objects.create_user("stranger", password="pw")

    def test_the_tab_renders_the_chain_and_the_table(self):
        _decide(self.roll, "One")
        self.client.force_login(self.alice)
        response = self.client.get(reverse("polls:snapshots"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chain-graph")
        self.assertContains(response, "sigma.min.js")
        self.assertContains(response, "One")

    def test_choosing_a_roll_you_are_not_on_is_a_404(self):
        self.client.force_login(self.alice)
        url = reverse("polls:snapshots") + f"?electorate={self.other.slug}"
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_taking_and_deleting_from_the_page(self):
        _decide(self.roll, "One")
        self.client.force_login(self.alice)
        take = reverse("polls:snapshot_take") + f"?electorate={self.roll.slug}"
        self.assertEqual(self.client.post(take).status_code, 302)
        snapshot = snapshots.LedgerSnapshot.objects.get()
        # Pressing it again resolves to the same state, not a second row.
        self.client.post(take)
        self.assertEqual(snapshots.LedgerSnapshot.objects.count(), 1)

        self.client.post(reverse("polls:snapshot_delete", args=[snapshot.pk]))
        self.assertEqual(snapshots.LedgerSnapshot.objects.count(), 0)
        self.assertEqual(Decision.objects.count(), 1)

    def test_a_stranger_cannot_delete_someone_elses_snapshot(self):
        _decide(self.roll, "One")
        snapshot, _ = snapshots.snapshot_current(self.roll)
        self.client.force_login(self.stranger)
        response = self.client.post(
            reverse("polls:snapshot_delete", args=[snapshot.pk]))
        self.assertEqual(response.status_code, 404)
        self.assertEqual(snapshots.LedgerSnapshot.objects.count(), 1)

    def test_the_verify_page_reports_match_and_differs(self):
        _decide(self.roll, "One")
        snapshot, _ = snapshots.snapshot_current(self.roll)
        self.client.force_login(self.alice)
        url = reverse("polls:snapshot_verify")
        response = self.client.post(url, {"payload": snapshot.payload})
        self.assertContains(response, "MATCH")

        _decide(self.roll, "Two")
        response = self.client.post(url, {"payload": snapshot.payload})
        self.assertContains(response, "DIFFERS")
