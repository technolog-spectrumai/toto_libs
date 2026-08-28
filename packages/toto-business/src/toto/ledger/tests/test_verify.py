"""What the walk catches, and where it says the chain broke.

**These tests have to defeat the triggers to do their job.** Migration 0002
refuses UPDATE and DELETE at the database, so a tamper cannot be staged through
any ordinary path — which is the point of it. But the triggers are not the last
line: an attacker with enough access to rewrite a row has enough access to drop
a trigger first. What survives that is the hash chain, and proving it does is
what this file is for. So each test here drops the triggers, corrupts the row
exactly as such an attacker would, and asks the verifier what it sees.
"""

from __future__ import annotations

from django.db import connection, transaction
from django.test import TestCase

from toto.ledger.canonical import canonical_payload
from toto.ledger.models import LedgerEntry, LedgerKind
from toto.ledger.services import chain

TABLE = LedgerEntry._meta.db_table


def _drop_triggers():
    with connection.cursor() as cursor:
        if connection.vendor == "postgresql":
            cursor.execute(f"DROP TRIGGER IF EXISTS bc_ledger_entry_no_update ON {TABLE}")
            cursor.execute(f"DROP TRIGGER IF EXISTS bc_ledger_entry_no_delete ON {TABLE}")
        else:
            cursor.execute("DROP TRIGGER IF EXISTS bc_ledger_entry_no_update")
            cursor.execute("DROP TRIGGER IF EXISTS bc_ledger_entry_no_delete")


def tamper(pk, **columns):
    """Rewrite a stored block the way somebody with database access would."""
    _drop_triggers()
    assignments = ", ".join(f"{name} = %s" for name in columns)
    with connection.cursor() as cursor:
        cursor.execute(
            f"UPDATE {TABLE} SET {assignments} WHERE id = %s",
            [*columns.values(), pk],
        )


def erase(pk):
    _drop_triggers()
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {TABLE} WHERE id = %s", [pk])


class VerifyTestCase(TestCase):
    def setUp(self):
        self.ledger = chain.open_ledger(
            key="acme-actions", name="Acme actions", kind=LedgerKind.COMPANY,
            scope_type="company.company",
        )
        self.blocks = [
            chain.append(ledger=self.ledger, payload={"n": n, "body": f"action {n}"})
            for n in range(1, 5)
        ]


class HealthyChainTests(VerifyTestCase):
    def test_an_untouched_chain_verifies(self):
        result = chain.verify(self.ledger)
        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 5)          # genesis + four
        self.assertIsNone(result.first_bad_sequence)
        self.assertEqual(result.status, "healthy")

    def test_the_verdict_is_truthy(self):
        self.assertTrue(chain.verify(self.ledger))

    def test_verification_writes_nothing(self):
        before = list(
            LedgerEntry.objects.filter(ledger=self.ledger)
            .order_by("sequence")
            .values("entry_hash", "payload_xml", "previous_hash")
        )
        chain.verify(self.ledger)
        after = list(
            LedgerEntry.objects.filter(ledger=self.ledger)
            .order_by("sequence")
            .values("entry_hash", "payload_xml", "previous_hash")
        )
        self.assertEqual(before, after)


class TamperDetectionTests(VerifyTestCase):
    def test_a_rewritten_payload_is_caught(self):
        tamper(self.blocks[1].pk, payload_xml=canonical_payload({"n": 999}))
        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 3)
        self.assertIn("hash does not verify", result.detail)

    def test_a_rewritten_actor_is_caught(self):
        tamper(self.blocks[0].pk, actor_ref="somebody-else")
        self.assertEqual(chain.verify(self.ledger).first_bad_sequence, 2)

    def test_a_rewritten_source_reference_is_caught(self):
        tamper(self.blocks[0].pk, source_ref="a different thing")
        self.assertFalse(chain.verify(self.ledger).ok)

    def test_a_rewritten_timestamp_is_caught(self):
        tamper(self.blocks[2].pk, occurred_at="1999-01-01 00:00:00")
        self.assertFalse(chain.verify(self.ledger).ok)

    def test_a_rewritten_hash_is_caught(self):
        tamper(self.blocks[0].pk, entry_hash="0" * 64)
        self.assertEqual(chain.verify(self.ledger).first_bad_sequence, 2)

    def test_a_self_consistent_single_row_rewrite_is_still_caught(self):
        """Rewriting the payload AND its own hash breaks the NEXT block's link."""
        forged_payload = canonical_payload({"n": 999})
        entry = LedgerEntry.objects.get(pk=self.blocks[1].pk)
        entry.payload_xml = forged_payload
        forged_hash = chain.entry_hash(entry)
        tamper(self.blocks[1].pk, payload_xml=forged_payload, entry_hash=forged_hash)

        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        # Block 3 now verifies against itself; block 4 no longer links to it.
        self.assertEqual(result.first_bad_sequence, 4)
        self.assertIn("previous hash", result.detail.lower())

    def test_a_deleted_block_breaks_contiguity(self):
        erase(self.blocks[1].pk)
        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 4)
        self.assertIn("contiguous", result.detail)

    def test_the_algorithm_cannot_be_swapped_out_from_under_the_chain(self):
        """Genesis is authoritative, so re-pointing a block is caught."""
        tamper(self.blocks[0].pk, algorithm="sha512")
        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        self.assertIn("different hash algorithm", result.detail)

    def test_repointing_the_ledger_row_does_not_help_an_attacker(self):
        """`Ledger.algorithm` is a convenience column; genesis is the truth."""
        self.ledger.algorithm = "sha512"
        self.ledger.save(update_fields=["algorithm"])
        self.assertEqual(chain.chain_algorithm(self.ledger), "sha256")
        self.assertTrue(chain.verify(self.ledger))


class ShapeTests(VerifyTestCase):
    def test_a_chain_that_does_not_begin_with_genesis_is_refused(self):
        erase(self.ledger.entries.get(sequence=1).pk)
        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 2)

    def test_a_second_genesis_mid_chain_is_refused(self):
        tamper(self.blocks[1].pk, source_type=LedgerEntry.GENESIS_SOURCE_TYPE)
        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        self.assertIn("second genesis", result.detail)

    def test_an_empty_chain_is_not_healthy(self):
        from toto.ledger.models import Ledger

        bare = Ledger.objects.create(key="bare", name="Bare")
        result = chain.verify(bare)
        self.assertFalse(result.ok)
        self.assertIn("no genesis", result.detail)

    def test_a_chain_sealed_with_an_unknown_algorithm_refuses_to_report_healthy(self):
        genesis = self.ledger.entries.get(sequence=1)
        forged = genesis.payload_xml.replace("sha256", "rot13")
        tamper(genesis.pk, payload_xml=forged)
        result = chain.verify(self.ledger)
        self.assertFalse(result.ok)
        self.assertIn("cannot verify", result.detail)


class SourceValidatorTests(VerifyTestCase):
    def test_a_source_validator_can_fail_a_block(self):
        def refuse_everything(entry):
            return "" if entry.is_genesis else "the source no longer exists"

        result = chain.verify(self.ledger, source_validator=refuse_everything)
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 2)
        self.assertIn("no longer exists", result.detail)

    def test_a_silent_validator_leaves_the_chain_healthy(self):
        self.assertTrue(chain.verify(self.ledger, source_validator=lambda entry: ""))


class RollbackTests(TestCase):
    def test_a_failure_inside_the_transaction_leaves_no_block(self):
        ledger = chain.open_ledger(key="acme", name="Acme")
        chain.append(ledger=ledger, payload={"n": 1})
        before = ledger.entries.count()

        class Boom(RuntimeError):
            pass

        with self.assertRaises(Boom):
            with transaction.atomic():
                chain.append(ledger=ledger, payload={"n": 2})
                raise Boom("the caller failed after appending")

        self.assertEqual(ledger.entries.count(), before)
        self.assertTrue(chain.verify(ledger))
