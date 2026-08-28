"""The audit chain: what it records, what it refuses, and how it detects tampering."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase

from toto.audit import record
from toto.audit.context import suppress_audit
from toto.audit.canonical import canonical_json, payload_hash
from toto.audit.models import AuditRecord
from toto.audit.services import record_material, verify_chain
from toto.core.models import Platform


class CanonicalJSONTests(TestCase):
    def test_key_order_does_not_change_the_digest(self):
        self.assertEqual(
            payload_hash({"a": 1, "b": 2}),
            payload_hash({"b": 2, "a": 1}),
        )

    def test_a_decimal_is_a_string_and_never_a_float(self):
        """A share count that round-trips through a float can change."""
        rendered = canonical_json({"units": Decimal("1000000.000001")})
        self.assertEqual(rendered, '{"units":"1000000.000001"}')

    def test_polish_text_hashes_as_itself(self):
        self.assertIn("spółka", canonical_json({"name": "spółka"}))


class AppendTests(TestCase):
    def test_the_first_record_starts_the_chain(self):
        entry = record("TEST_ACTION", app_label="company")
        self.assertEqual(entry.sequence, 1)
        self.assertEqual(entry.previous_hash, "")
        self.assertTrue(entry.record_hash)

    def test_each_record_links_to_its_predecessor(self):
        first = record("ONE", app_label="company")
        second = record("TWO", app_label="company")
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.previous_hash, first.record_hash)

    def test_a_dedupe_key_returns_the_existing_record(self):
        first = record("ONCE", app_label="company", dedupe_key="only-once")
        again = record("ONCE", app_label="company", dedupe_key="only-once")
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(AuditRecord.objects.count(), 1)

    def test_an_object_is_identified_by_its_content_type(self):
        platform = Platform.objects.create(site_name="Test", author="Tests",
                                           publication_year=2026)
        entry = record("PLATFORM_CREATED", obj=platform)
        self.assertEqual(entry.object_type, "core.platform")
        self.assertEqual(entry.object_id, str(platform.pk))
        self.assertEqual(entry.app_label, "core")

    def test_before_and_after_become_a_change_set_of_only_what_moved(self):
        entry = record(
            "COMPANY_UPDATED", app_label="company",
            before={"name": "Old", "seat": "Warszawa"},
            after={"name": "New", "seat": "Warszawa"},
        )
        self.assertEqual(list(entry.changes), ["name"])
        self.assertEqual(entry.changes["name"], {"before": "Old", "after": "New"})

    def test_suppress_audit_writes_nothing(self):
        with suppress_audit():
            self.assertIsNone(record("QUIET", app_label="company"))
        self.assertEqual(AuditRecord.objects.count(), 0)


class RedactionTests(TestCase):
    def test_a_sensitive_key_is_redacted(self):
        entry = record("LOGIN", app_label="core",
                       metadata={"password": "hunter2", "token": "abc", "username": "anna"})
        self.assertEqual(entry.metadata["password"], "[REDACTED]")
        self.assertEqual(entry.metadata["token"], "[REDACTED]")
        self.assertEqual(entry.metadata["username"], "anna")

    def test_a_secret_hiding_in_a_value_is_redacted(self):
        entry = record("CONFIG", app_label="core",
                       metadata={"note": "set PASSWORD=hunter2 in the env"})
        self.assertEqual(entry.metadata["note"], "[REDACTED]")

    def test_nested_structures_are_redacted_too(self):
        entry = record("CONFIG", app_label="core",
                       metadata={"db": {"user": "placidia", "db_password": "s3cret"}})
        self.assertEqual(entry.metadata["db"]["db_password"], "[REDACTED]")
        self.assertEqual(entry.metadata["db"]["user"], "placidia")

    def test_an_unbounded_string_is_clamped(self):
        """An audit row that can carry a blob is a way to fill the disk."""
        entry = record("NOTE", app_label="core", metadata={"body": "x" * 10000})
        self.assertEqual(len(entry.metadata["body"]), 4000)


class AppendOnlyTests(TestCase):
    def test_a_record_cannot_be_edited(self):
        entry = record("ONE", app_label="company")
        entry.action = "TAMPERED"
        with self.assertRaises(ValidationError):
            entry.save()

    def test_a_record_cannot_be_deleted(self):
        entry = record("ONE", app_label="company")
        with self.assertRaises(ValidationError):
            entry.delete()

    def test_a_hand_built_record_without_a_hash_is_refused(self):
        from toto.audit.models import AuditChain

        chain, _ = AuditChain.objects.get_or_create(key="placidia-activity")
        with self.assertRaises(ValidationError):
            AuditRecord(chain=chain, sequence=1, action="FORGED",
                        app_label="company").save()


class VerifyTests(TestCase):
    def setUp(self):
        for index in range(5):
            record(f"ACTION_{index}", app_label="company")

    def test_an_untouched_chain_verifies(self):
        result = verify_chain()
        self.assertTrue(result.ok)
        self.assertEqual(result.checked, 5)
        self.assertEqual(result.status, "healthy")

    def test_an_empty_chain_verifies(self):
        # _raw_delete goes under the model's append-only guard, which is the
        # only way to empty the table and also how a real tamperer would work.
        AuditRecord.objects.all()._raw_delete(AuditRecord.objects.db)
        self.assertTrue(verify_chain().ok)

    def test_a_deleted_record_breaks_the_sequence(self):
        """Deletion is blocked in Python, so a tamperer goes around the model."""
        AuditRecord.objects.filter(sequence=3)._raw_delete(AuditRecord.objects.db)
        result = verify_chain()
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 4)
        self.assertIn("contiguous", result.detail)

    def test_an_edited_record_no_longer_hashes_to_its_own_digest(self):
        AuditRecord.objects.filter(sequence=2).update(object_description="rewritten")
        result = verify_chain()
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 2)
        self.assertIn("edited", result.detail)

    def test_rehashing_an_edited_record_still_breaks_the_next_link(self):
        """The chain, not the row, is what makes an edit unfixable in place."""
        entry = AuditRecord.objects.get(sequence=2)
        entry.object_description = "rewritten"
        forged = payload_hash(record_material(entry))
        AuditRecord.objects.filter(sequence=2).update(
            object_description="rewritten", record_hash=forged)
        result = verify_chain()
        self.assertFalse(result.ok)
        self.assertEqual(result.first_bad_sequence, 3)
        self.assertIn("Previous hash", result.detail)
