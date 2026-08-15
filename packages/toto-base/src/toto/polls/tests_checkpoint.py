"""Ledger checkpoints: the fold, the payload, and the offline verdicts.

The claim under test is the one verify_chain cannot make: a checkpoint kept
OUTSIDE the database convicts even a self-consistent rewrite — content
edited and every later hash recomputed — and, when it convicts, it says
where the damage is or how to bracket it.

Run only where a gate stanza names this module.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from . import checkpoint, services
from .checkpoint_models import LedgerCheckpoint
from .models import Decision, compute_hash
from .tests_ledger_chain import _decided, _platform

User = get_user_model()


class FoldTests(TestCase):
    def test_the_fold_is_deterministic_and_grows(self):
        _decided("One")
        count1, head1 = checkpoint.head_at("", "")
        count1b, head1b = checkpoint.head_at("", "")
        self.assertEqual((count1, head1), (count1b, head1b))
        _decided("Two")
        count2, head2 = checkpoint.head_at("", "")
        self.assertEqual((count1, count2), (1, 2))
        self.assertNotEqual(head1, head2)
        # The old head is still the head at count 1 — a fold, not a digest
        # of the whole: old checkpoints stay verifiable as prefixes.
        self.assertEqual(checkpoint.head_at("", "", upto=1)[1], head1)

    def test_scopes_fold_independently(self):
        _decided("Global")
        _decided("Scoped", scope_type="forum.channel", scope_id="1")
        self.assertNotEqual(checkpoint.head_at("", "")[1],
                            checkpoint.head_at("forum.channel", "1")[1])

    def test_payload_round_trips(self):
        _decided("One")
        stored = checkpoint.take(scope_type="", scope_id="")
        parsed = checkpoint.parse_payload(stored.payload)
        self.assertEqual(parsed["entry_count"], 1)
        self.assertEqual(parsed["head_hex"], stored.head_hash)
        self.assertEqual(parsed["scope_type"], "")

    def test_garbage_payloads_are_refused_with_a_sentence(self):
        for bad in ("", "https://example.com", "totoledger:v=2&a=x&n=1&h=h&t=t",
                    "totoledger:v=1&a=wrong&n=1&h=h&t=t",
                    "totoledger:v=1&a=" + checkpoint.ALGORITHM):
            with self.subTest(bad=bad[:30]):
                with self.assertRaises(checkpoint.PayloadError):
                    checkpoint.parse_payload(bad)


class VerdictTests(TestCase):
    def test_match_and_match_grown(self):
        _decided("One")
        stored = checkpoint.take(scope_type="", scope_id="")
        self.assertEqual(checkpoint.verify_stored(stored).verdict, "MATCH")
        _decided("Two")
        self.assertEqual(checkpoint.verify_stored(stored).verdict,
                         "MATCH_GROWN")

    def test_truncation_is_named_as_deletion(self):
        _decided("One")
        _decided("Two")
        stored = checkpoint.take(scope_type="", scope_id="")
        # QuerySet.delete bypasses Decision.delete — exactly the raw access
        # an attacker has.
        Decision.objects.filter(title="Two").delete()
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "TRUNCATED")
        self.assertIn("deleted", result.detail)

    def test_a_display_column_edit_is_localized_to_its_row(self):
        # The one verify_chain is BLIND to: the chain hashes only content,
        # so editing the rendered column passes the chain and must fail the
        # fold, naming the row.
        _decided("One")
        target = _decided("Two")
        _decided("Three")
        stored = checkpoint.take(scope_type="", scope_id="")

        Decision.objects.filter(pk=target.pk).update(outcome="winner",
                                                     winner_label="Forged")
        self.assertTrue(Decision.verify_chain().ok)

        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "MISMATCH")
        self.assertEqual(result.first_bad_pk, target.pk)
        self.assertIn("edited directly", result.detail)

    def test_a_consistent_rewrite_beats_the_chain_but_not_the_checkpoint(self):
        # The headline case: edit d2's content, recompute d2 and d3's hashes
        # so the chain verifies clean — and the checkpoint still convicts.
        _decided("One")
        d2 = _decided("Two")
        d3 = _decided("Three")
        stored = checkpoint.take(scope_type="", scope_id="")

        content = dict(d2.content)
        content["decision_comment"] = "history, improved"
        new_h2 = compute_hash(content, d2.prev_hash)
        Decision.objects.filter(pk=d2.pk).update(content=content,
                                                 content_hash=new_h2)
        Decision.objects.filter(pk=d3.pk).update(
            prev_hash=new_h2, content_hash=compute_hash(d3.content, new_h2))

        self.assertTrue(Decision.verify_chain().ok)
        result = checkpoint.verify_stored(stored)
        self.assertEqual(result.verdict, "MISMATCH")
        self.assertIn("self-consistently", result.detail)

    def test_bracketing_dates_a_consistent_rewrite(self):
        d1 = _decided("One")
        early = checkpoint.take(scope_type="", scope_id="")
        d2 = _decided("Two")
        late = checkpoint.take(scope_type="", scope_id="")

        content = dict(d2.content)
        content["decision_comment"] = "rewritten"
        Decision.objects.filter(pk=d2.pk).update(
            content=content,
            content_hash=compute_hash(content, d2.prev_hash))

        hit, miss = checkpoint.bracket("", "")
        self.assertEqual(hit.pk, early.pk)
        self.assertEqual(miss.pk, late.pk)

    def test_empty_scope_says_so(self):
        _decided("One")
        stored = checkpoint.take(scope_type="", scope_id="")
        parsed = checkpoint.parse_payload(stored.payload)
        parsed["scope_type"] = "forum.channel"
        parsed["scope_id"] = "99"
        self.assertEqual(checkpoint.verify(parsed).verdict, "EMPTY_SCOPE")


class GuardTests(TestCase):
    def test_checkpoint_rows_refuse_edits_and_deletes(self):
        _decided("One")
        stored = checkpoint.take(scope_type="", scope_id="")
        stored.note = "changed"
        with self.assertRaises(ValueError):
            stored.save()
        with self.assertRaises(ValueError):
            stored.delete()

    def test_non_canonical_content_is_refused_at_write_time(self):
        from datetime import datetime

        with self.assertRaises(ValueError):
            services._assert_canonical({"when": datetime(2026, 1, 1)})
        with self.assertRaises(ValueError):
            services._assert_canonical({1: "non-string key"})
        services._assert_canonical(
            {"ok": ["text", 1, 1.5, True, None, {"nested": []}]})


class CommandTests(TestCase):
    def test_the_command_passes_clean_and_fails_on_tamper(self):
        target = _decided("One")
        checkpoint.take(scope_type="", scope_id="")
        call_command("verify_ledger", verbosity=0)

        Decision.objects.filter(pk=target.pk).update(outcome="tie")
        with self.assertRaises(SystemExit):
            call_command("verify_ledger", verbosity=0)

    def test_the_command_verifies_a_pasted_payload(self):
        _decided("One")
        stored = checkpoint.take(scope_type="", scope_id="")
        call_command("verify_ledger", payload=stored.payload, verbosity=0)


class PageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        _platform()
        cls.staff = User.objects.create_user("op", password="x", is_staff=True)
        cls.member = User.objects.create_user("m", password="x")

    def test_taking_is_staff_only_verifying_is_not(self):
        _decided("One")
        self.client.force_login(self.member)
        resp = self.client.post(reverse("polls:ledger_checkpoint_new"))
        self.assertEqual(resp.status_code, 403)

        self.client.force_login(self.staff)
        resp = self.client.post(reverse("polls:ledger_checkpoint_new"),
                                {"note": "audit"})
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(LedgerCheckpoint.objects.count(), 1)

        self.client.force_login(self.member)
        stored = LedgerCheckpoint.objects.get()
        resp = self.client.post(reverse("polls:ledger_checkpoint_verify"),
                                {"payload": stored.payload})
        self.assertContains(resp, "MATCH")

    def test_the_list_badges_a_tampered_ledger(self):
        target = _decided("One")
        self.client.force_login(self.staff)
        self.client.post(reverse("polls:ledger_checkpoint_new"))
        Decision.objects.filter(pk=target.pk).update(outcome="tie")
        resp = self.client.get(reverse("polls:ledger_checkpoints"))
        self.assertContains(resp, "MISMATCH")

    def test_pdf_export_with_qr_takes_and_embeds_a_checkpoint(self):
        _decided("One")
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("polls:ledger_pdf") + "?qr=1")
        if resp.status_code == 503:
            self.skipTest("no PDF renderer in this environment")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        stored = LedgerCheckpoint.objects.get()
        self.assertIn("ledger PDF export", stored.note)

    def test_pdf_export_without_qr_takes_no_checkpoint(self):
        _decided("One")
        self.client.force_login(self.staff)
        resp = self.client.get(reverse("polls:ledger_pdf"))
        if resp.status_code == 503:
            self.skipTest("no PDF renderer in this environment")
        self.assertEqual(LedgerCheckpoint.objects.count(), 0)
