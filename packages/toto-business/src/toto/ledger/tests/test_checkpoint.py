"""The QR checkpoint: the head of the chain, kept where the database is not.

Ported from `limbo/polls_governance/tests_checkpoint.py` and rewired from
Decisions to blocks. What these prove is the one thing the chain cannot prove
about itself: an attacker who rewrites a payload AND recomputes every later
hash produces a chain that verifies clean — and a checkpoint taken beforehand
still says no.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from toto.ledger import checkpoint
from toto.ledger.canonical import canonical_payload
from toto.ledger.models import LedgerCheckpoint, LedgerEntry
from toto.ledger.services import chain
from toto.ledger.tests.test_verify import tamper


class CheckpointTestCase(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("ada", password="x")
        self.ledger = chain.open_ledger(key="acme", name="Acme actions")
        self.blocks = [
            chain.append(ledger=self.ledger, payload={"n": n, "body": f"action {n}"})
            for n in (1, 2, 3)
        ]


class FoldTests(CheckpointTestCase):
    def test_the_fold_is_stable_across_calls(self):
        first = checkpoint.head_at(checkpoint.SCOPE_TYPE, str(self.ledger.uid))
        second = checkpoint.head_at(checkpoint.SCOPE_TYPE, str(self.ledger.uid))
        self.assertEqual(first, second)
        self.assertEqual(first[0], 4)          # genesis + three

    def test_one_walk_yields_a_head_at_every_count(self):
        heads = list(checkpoint.fold_heads(
            LedgerEntry.objects.filter(ledger=self.ledger).order_by("sequence")
        ))
        self.assertEqual([count for count, _h, _pk in heads], [1, 2, 3, 4])
        self.assertEqual(len({h for _c, h, _pk in heads}), 4)

    def test_a_prefix_head_matches_what_that_prefix_folded_to(self):
        full = list(checkpoint.fold_heads(
            LedgerEntry.objects.filter(ledger=self.ledger).order_by("sequence")
        ))
        count, head = checkpoint.head_at(checkpoint.SCOPE_TYPE,
                                         str(self.ledger.uid), upto=2)
        self.assertEqual((count, head), (full[1][0], full[1][1]))

    def test_an_unknown_scope_folds_to_nothing(self):
        self.assertEqual(checkpoint.head_at("something-else", "x"), (0, ""))


class PayloadTests(CheckpointTestCase):
    def test_a_payload_round_trips(self):
        stored = checkpoint.take(ledger=self.ledger, by=self.user)
        parsed = checkpoint.parse_payload(stored.payload)
        self.assertEqual(parsed["entry_count"], 4)
        self.assertEqual(parsed["head_hex"], stored.head_hash)
        self.assertEqual(parsed["scope_type"], checkpoint.SCOPE_TYPE)
        self.assertEqual(parsed["scope_id"], str(self.ledger.uid))

    def test_text_that_is_not_a_checkpoint_is_refused_by_name(self):
        with self.assertRaises(checkpoint.PayloadError) as caught:
            checkpoint.parse_payload("https://example.com/")
        self.assertIn("not a ledger checkpoint", str(caught.exception))

    def test_an_unknown_version_refuses_rather_than_guessing(self):
        stored = checkpoint.take(ledger=self.ledger)
        with self.assertRaises(checkpoint.PayloadError):
            checkpoint.parse_payload(stored.payload.replace("v=1", "v=9"))

    def test_an_unknown_algorithm_refuses_rather_than_guessing(self):
        stored = checkpoint.take(ledger=self.ledger)
        broken = stored.payload.replace(checkpoint.ALGORITHM, "sha256-fold-ns9")
        with self.assertRaises(checkpoint.PayloadError) as caught:
            checkpoint.parse_payload(broken)
        self.assertIn("refuses to guess", str(caught.exception))

    def test_a_truncated_scan_says_so(self):
        stored = checkpoint.take(ledger=self.ledger)
        head, _sep, _rest = stored.payload.partition("&h=")
        with self.assertRaises(checkpoint.PayloadError) as caught:
            checkpoint.parse_payload(head)
        self.assertIn("scan the whole code", str(caught.exception))


class TakeTests(CheckpointTestCase):
    def test_taking_stores_the_exact_text_that_will_be_printed(self):
        stored = checkpoint.take(ledger=self.ledger, by=self.user, note="pre-audit")
        self.assertTrue(stored.payload.startswith(checkpoint.SCHEME))
        self.assertEqual(stored.entry_count, 4)
        self.assertEqual(stored.taken_by, self.user)
        self.assertEqual(stored.note, "pre-audit")

    def test_a_checkpoint_is_never_edited(self):
        stored = checkpoint.take(ledger=self.ledger)
        stored.note = "changed"
        with self.assertRaises(ValidationError):
            stored.save()

    def test_a_checkpoint_is_never_deleted(self):
        stored = checkpoint.take(ledger=self.ledger)
        with self.assertRaises(ValidationError):
            stored.delete()

    def test_check_pointing_an_empty_chain_is_refused(self):
        """An empty fold would write a payload the parser then refuses — and
        the row is undeletable, so it would break the page for good."""
        from toto.ledger.models import Ledger

        bare = Ledger.objects.create(key="bare", name="Bare")
        with self.assertRaises(ValueError):
            checkpoint.take(ledger=bare)
        self.assertFalse(LedgerCheckpoint.objects.exists())


class VerifyTests(CheckpointTestCase):
    def test_an_untouched_chain_matches(self):
        stored = checkpoint.take(ledger=self.ledger)
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "MATCH")
        self.assertEqual(result.checked, 4)

    def test_verify_text_needs_no_stored_row(self):
        """A QR from a year ago, against a database whose rows were deleted."""
        stored = checkpoint.take(ledger=self.ledger)
        payload = stored.payload
        LedgerCheckpoint.objects.all().delete()
        self.assertEqual(checkpoint.verify_text(payload).verdict, "MATCH")

    def test_growth_is_not_tampering(self):
        stored = checkpoint.take(ledger=self.ledger)
        chain.append(ledger=self.ledger, payload={"n": 4})
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "MATCH_GROWN")
        self.assertIn("prefix still matches", result.detail)

    def test_A_SELF_CONSISTENT_REWRITE_IS_CAUGHT(self):
        """The case the chain cannot catch, and the whole reason for this file.

        Rewrite a payload, recompute that block's hash, and recompute every
        later block's previous_hash and hash. The chain then verifies clean —
        and the checkpoint taken beforehand still says MISMATCH.
        """
        stored = checkpoint.take(ledger=self.ledger)
        self.assertEqual(checkpoint.verify_stored(stored).verdict, "MATCH")

        # The attacker: edit block 2, then re-hash the whole tail.
        forged = canonical_payload({"n": 999, "body": "forged"})
        tamper(self.blocks[0].pk, payload_xml=forged)
        previous = ""
        for entry in LedgerEntry.objects.filter(ledger=self.ledger).order_by("sequence"):
            entry.previous_hash = previous
            fresh = chain.entry_hash(entry)
            tamper(entry.pk, previous_hash=previous, entry_hash=fresh)
            previous = fresh

        # The chain now agrees with itself, exactly as designed.
        self.assertTrue(chain.verify(self.ledger).ok)

        # The paper does not.
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "MISMATCH")
        self.assertIn("rewritten self-consistently", result.detail)

    def test_a_careless_edit_is_localized_to_the_block(self):
        stored = checkpoint.take(ledger=self.ledger)
        tamper(self.blocks[1].pk, payload_xml=canonical_payload({"n": 0}))
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "MISMATCH")
        self.assertIn("hash chain itself is broken", result.detail)
        self.assertEqual(result.first_bad_pk, 3)

    def test_deleted_blocks_report_TRUNCATED(self):
        stored = checkpoint.take(ledger=self.ledger)
        from toto.ledger.tests.test_verify import erase

        erase(self.blocks[2].pk)
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "TRUNCATED")
        self.assertIn("deleted outright", result.detail)

    def test_a_checkpoint_of_a_chain_that_is_gone_says_so(self):
        stored = checkpoint.take(ledger=self.ledger)
        payload = stored.payload.replace(
            str(self.ledger.uid), "00000000-0000-0000-0000-000000000000")
        self.assertEqual(checkpoint.verify_text(payload).verdict, "EMPTY_SCOPE")


class BracketTests(CheckpointTestCase):
    def test_the_newest_matching_checkpoint_bounds_the_last_honest_state(self):
        early = checkpoint.take(ledger=self.ledger, note="early")
        chain.append(ledger=self.ledger, payload={"n": 4})
        late = checkpoint.take(ledger=self.ledger, note="late")

        # Rewrite everything self-consistently.
        tamper(self.blocks[0].pk, payload_xml=canonical_payload({"n": 999}))
        previous = ""
        for entry in LedgerEntry.objects.filter(ledger=self.ledger).order_by("sequence"):
            fresh_prev = previous
            entry.previous_hash = fresh_prev
            fresh = chain.entry_hash(entry)
            tamper(entry.pk, previous_hash=fresh_prev, entry_hash=fresh)
            previous = fresh

        matching, failing = checkpoint.bracket(checkpoint.SCOPE_TYPE,
                                               str(self.ledger.uid))
        self.assertIsNone(matching)
        self.assertIsNotNone(failing)
        del early, late

    def test_with_nothing_wrong_the_newest_checkpoint_matches(self):
        checkpoint.take(ledger=self.ledger)
        matching, failing = checkpoint.bracket(checkpoint.SCOPE_TYPE,
                                               str(self.ledger.uid))
        self.assertIsNotNone(matching)
        self.assertIsNone(failing)
