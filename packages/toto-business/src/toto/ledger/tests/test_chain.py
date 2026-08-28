"""Genesis, appending, and the walk that has to catch a tamper.

irena shipped this engine with no tests of its own — its coverage lived in the
pre-split `irene/tests.py` and went to the grave with that app. This file is
the battery it never had.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from toto.ledger.canonical import canonical_payload, digest
from toto.ledger.models import Ledger, LedgerEntry, LedgerKind
from toto.ledger.services import chain


class ChainTestCase(TestCase):
    def open(self, key="acme-actions", **kwargs):
        return chain.open_ledger(
            key=key, name="Acme actions", kind=LedgerKind.COMPANY,
            scope_type="company.company", **kwargs,
        )


class GenesisTests(ChainTestCase):
    def test_a_new_chain_has_exactly_one_block_and_it_is_genesis(self):
        ledger = self.open()
        self.assertEqual(ledger.entries.count(), 1)
        genesis = ledger.entries.get()
        self.assertTrue(genesis.is_genesis)
        self.assertEqual(genesis.sequence, 1)
        self.assertEqual(genesis.previous_hash, "")

    def test_the_genesis_block_records_how_to_read_the_chain(self):
        ledger = self.open()
        payload = ledger.entries.get().payload_xml
        self.assertIn("hash_algorithm", payload)
        self.assertIn("sha256", payload)
        self.assertIn("format_version", payload)
        self.assertIn("bc-ledger-xml-1", payload)

    def test_opening_the_same_chain_twice_is_idempotent(self):
        first = self.open()
        second = self.open()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(second.entries.count(), 1)

    def test_an_unknown_algorithm_is_refused_at_creation(self):
        from toto.ledger.canonical import UnknownAlgorithm

        with self.assertRaises(UnknownAlgorithm):
            self.open(key="bad", algorithm="rot13")

    def test_a_chain_may_be_sealed_with_something_other_than_sha256(self):
        ledger = self.open(key="strong", algorithm="sha512")
        self.assertEqual(chain.chain_algorithm(ledger), "sha512")
        self.assertTrue(chain.verify(ledger))

    def test_appending_to_a_chain_with_no_genesis_is_refused(self):
        bare = Ledger.objects.create(key="bare", name="Bare")
        with self.assertRaises(ValidationError):
            chain.append(ledger=bare, payload={"x": 1})

    def test_two_scopes_may_use_the_same_key(self):
        import uuid

        one = chain.open_ledger(key="actions", name="One",
                                scope_type="company.company", scope_uid=uuid.uuid4())
        two = chain.open_ledger(key="actions", name="Two",
                                scope_type="company.company", scope_uid=uuid.uuid4())
        self.assertNotEqual(one.pk, two.pk)


class AppendTests(ChainTestCase):
    def setUp(self):
        self.ledger = self.open()

    def test_a_block_links_to_the_one_before_it(self):
        first = chain.append(ledger=self.ledger, payload={"n": 1})
        second = chain.append(ledger=self.ledger, payload={"n": 2})
        genesis = self.ledger.entries.get(sequence=1)
        self.assertEqual(first.previous_hash, genesis.entry_hash)
        self.assertEqual(second.previous_hash, first.entry_hash)
        self.assertEqual([1, 2, 3], list(
            self.ledger.entries.order_by("sequence").values_list("sequence", flat=True)
        ))

    def test_the_payload_is_frozen_as_canonical_xml(self):
        entry = chain.append(ledger=self.ledger, payload={"units": Decimal("1.5")})
        self.assertEqual(entry.payload_xml, canonical_payload({"units": Decimal("1.5")}))
        self.assertIn("<decimal>1.5</decimal>", entry.payload_xml)

    def test_xml_text_and_a_structure_reach_the_same_block(self):
        by_structure = chain.append(ledger=self.ledger, payload={"a": "it's"})
        other = self.open(key="second")
        by_text = chain.append(
            ledger=other, payload_xml=canonical_payload({"a": "it's"}),
        )
        self.assertEqual(by_structure.payload_xml, by_text.payload_xml)

    def test_passing_both_or_neither_payload_is_refused(self):
        with self.assertRaises(ValidationError):
            chain.append(ledger=self.ledger, payload={"a": 1}, payload_xml="<payload/>")
        with self.assertRaises(ValidationError):
            chain.append(ledger=self.ledger)

    def test_appending_the_same_source_twice_returns_the_first_block(self):
        import uuid

        source = uuid.uuid4()
        first = chain.append(ledger=self.ledger, payload={"n": 1},
                             source_type="company.action", source_uid=source)
        again = chain.append(ledger=self.ledger, payload={"n": 999},
                             source_type="company.action", source_uid=source)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(self.ledger.entries.count(), 2)

    def test_every_block_has_a_globally_unique_id(self):
        entries = [chain.append(ledger=self.ledger, payload={"n": n}) for n in range(5)]
        uids = {entry.uid for entry in entries}
        self.assertEqual(len(uids), 5)

    def test_a_block_id_cited_in_a_payload_stays_ordinary_text(self):
        """The ledger interprets nothing. A citation is a string, not a link."""
        first = chain.append(ledger=self.ledger, payload={"n": 1})
        second = chain.append(
            ledger=self.ledger,
            payload={"body": f"This corrects block {first.uid}."},
        )
        self.assertIn(str(first.uid), second.payload_xml)
        self.assertIsNone(second.source_uid)
        field_names = {f.name for f in LedgerEntry._meta.get_fields()}
        self.assertNotIn("supersedes", field_names)
        self.assertNotIn("corrects", field_names)

    def test_a_payload_that_cannot_be_canonicalized_appends_nothing(self):
        before = self.ledger.entries.count()
        with self.assertRaises(Exception):
            chain.append(ledger=self.ledger, payload={"bad": 0.1})
        self.assertEqual(self.ledger.entries.count(), before)


class ForkPreventionTests(ChainTestCase):
    """The lock makes sequences contiguous; the constraints make a fork impossible."""

    def setUp(self):
        self.ledger = self.open()
        self.first = chain.append(ledger=self.ledger, payload={"n": 1})

    def _forge(self, **overrides):
        fields = dict(
            ledger=self.ledger,
            sequence=self.first.sequence,
            previous_hash=self.first.previous_hash,
            payload_xml=canonical_payload({"n": "forged"}),
            algorithm=self.ledger.algorithm,
            format_version=self.ledger.format_version,
        )
        fields.update(overrides)
        entry = LedgerEntry(**fields)
        entry.entry_hash = digest(
            chain.entry_block_xml(entry), algorithm=entry.algorithm,
        )
        return entry

    def test_two_blocks_cannot_share_a_sequence(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._forge().save()

    def test_two_blocks_cannot_share_a_predecessor(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._forge(sequence=99).save()

    def test_two_blocks_cannot_share_a_source(self):
        import uuid

        source = uuid.uuid4()
        chain.append(ledger=self.ledger, payload={"n": 2},
                     source_type="x", source_uid=source)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._forge(sequence=98, previous_hash="ff",
                        source_type="x", source_uid=source).save()


class ImmutabilityTests(ChainTestCase):
    def setUp(self):
        self.ledger = self.open()
        self.entry = chain.append(ledger=self.ledger, payload={"n": 1})

    def test_the_orm_refuses_to_update_a_block(self):
        self.entry.payload_xml = canonical_payload({"n": 2})
        with self.assertRaises(ValidationError):
            self.entry.save()

    def test_the_orm_refuses_to_delete_a_block(self):
        with self.assertRaises(ValidationError):
            self.entry.delete()

    def test_a_block_cannot_be_created_without_going_through_the_engine(self):
        entry = LedgerEntry(
            ledger=self.ledger, sequence=99, payload_xml="<payload/>",
        )
        with self.assertRaises(ValidationError):
            entry.save()

    def test_the_database_refuses_a_queryset_update(self):
        """`.update()` never calls save(), so the Python guard cannot see it."""
        with self.assertRaises(Exception), transaction.atomic():
            LedgerEntry.objects.filter(pk=self.entry.pk).update(payload_xml="<payload/>")

    def test_the_database_refuses_a_queryset_delete(self):
        with self.assertRaises(Exception), transaction.atomic():
            LedgerEntry.objects.filter(pk=self.entry.pk).delete()

    def test_the_database_refuses_raw_sql(self):
        """The prompt a determined operator would actually use."""
        table = LedgerEntry._meta.db_table
        with self.assertRaises(Exception), transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    f"UPDATE {table} SET payload_xml = %s WHERE id = %s",
                    ["<payload/>", self.entry.pk],
                )

    def test_the_row_is_still_there_after_every_refusal(self):
        self.assertTrue(LedgerEntry.objects.filter(pk=self.entry.pk).exists())


class ImmutabilityMigrationTests(TestCase):
    """The trigger migration, on the engine this gate does not run.

    A real deploy failed here with `IndexError: tuple index out of range` from
    inside psycopg, during `migrate`, on Postgres. The cause: the plpgsql
    function body carries a RAISE EXCEPTION format string —

        'ledger entries are append-only: % on %.% is refused'

    — and `schema_editor.execute(sql)` defaults to `params=()`, which sends the
    SQL through the driver's parameter interpolation. Those three `%` are read
    as placeholders against an empty tuple.

    **The gate could not have caught it.** Its migration stage runs against a
    throwaway SQLite database, so the postgresql branch never executes. These
    tests therefore assert the CALL rather than the effect: what matters is that
    the SQL is handed over uninterpolated, and that is checkable on any engine.
    """

    def _fake_editor(self, vendor):
        from unittest import mock

        editor = mock.Mock()
        editor.connection.vendor = vendor
        return editor

    def _migration(self):
        import importlib.util
        from pathlib import Path

        path = (Path(__file__).resolve().parent.parent
                / "migrations" / "0002_immutability_triggers.py")
        spec = importlib.util.spec_from_file_location("ledger_mig_0002", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_trigger_body_still_contains_the_placeholders(self):
        """The premise. If the message loses its `%`, these tests prove nothing
        and should be deleted rather than left passing."""
        module = self._migration()
        self.assertIn("%", module.POSTGRES_UP)

    def test_postgres_sql_is_executed_without_interpolation(self):
        module = self._migration()
        editor = self._fake_editor("postgresql")

        module.install(None, editor)

        _args, kwargs = editor.execute.call_args
        self.assertIsNone(
            kwargs.get("params", ()),
            "params must be None: the default () makes psycopg read the "
            "RAISE EXCEPTION format string's % as placeholders")

    def test_the_rollback_path_is_uninterpolated_too(self):
        """Kept even though POSTGRES_DOWN has no `%` today — the two halves are
        edited together, and a `%` added there would fail only on a rollback."""
        module = self._migration()
        editor = self._fake_editor("postgresql")

        module.remove(None, editor)

        _args, kwargs = editor.execute.call_args
        self.assertIsNone(kwargs.get("params", ()))

    def test_an_unknown_engine_refuses_rather_than_pretending(self):
        module = self._migration()
        with self.assertRaises(RuntimeError) as caught:
            module.install(None, self._fake_editor("oracle"))
        self.assertIn("oracle", str(caught.exception))
