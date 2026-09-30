"""`erase_user`'s report and its refusals, beyond the first suite (2026-09-29).

The console reads one JSON line and shows it to an operator who is about to
do something irreversible, so what the report says — what goes, what stays
detached, what blocks it, the notes — is the contract under test here, as
much as the erase itself."""

import io
import json
import tempfile
import unittest
from unittest import mock

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models.deletion import Collector, ProtectedError, RestrictedError
from django.test import TestCase, override_settings

from toto.audit.models import AuditRecord
from toto.audit.services import record, verify_chain
from toto.comments import services as comments
from toto.comments.models import Comment
from toto.core.management.commands.erase_user import plan

User = get_user_model()


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    try:
        call_command(*args, stdout=out, stderr=err)
        code = 0
    except SystemExit as exc:
        code = exc.code
    return code, json.loads(out.getvalue().strip().splitlines()[-1]), err.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="erase-more-"))
class EraseCase(TestCase):
    def setUp(self):
        self.root = User.objects.create_superuser("root", "r@example.com", "pw")
        self.ada = User.objects.create_user("ada", "ada@example.com", "pw")


class PlanReportTests(EraseCase):
    def test_the_report_names_the_account_and_writes_nothing(self):
        before = (User.objects.count(), AuditRecord.objects.count())
        report = plan(self.ada)
        self.assertEqual((report["username"], report["id"], report["superuser"]),
                         ("ada", self.ada.pk, False))
        self.assertEqual(report["deleted"]["auth.User"], 1)
        self.assertEqual(report["blocked_by"], [])
        self.assertEqual((User.objects.count(), AuditRecord.objects.count()), before)

    def test_a_superuser_is_flagged_as_one(self):
        self.assertTrue(plan(self.root)["superuser"])

    def test_every_report_says_what_the_chain_and_the_backups_keep(self):
        notes = " ".join(plan(self.ada)["notes"])
        self.assertIn("audit chain keeps their records", notes)
        self.assertIn("Backups taken before now", notes)

    def test_a_comment_they_wrote_is_listed_as_detached_not_deleted(self):
        comments.add(self.ada, "my two cents")
        report = plan(self.ada)
        self.assertEqual(report["detached"]["comments.Comment.author"], 1)
        self.assertNotIn("comments.Comment", report["deleted"])

    def test_the_report_is_sorted_so_two_runs_read_the_same(self):
        report = plan(self.ada)
        self.assertEqual(list(report["deleted"]), sorted(report["deleted"]))
        self.assertEqual(list(report["detached"]), sorted(report["detached"]))

    @unittest.skipUnless(apps.is_installed("toto.gitea"), "no forge on this host")
    def test_a_forge_account_brings_its_own_note(self):
        from toto.gitea.models import GiteaAccount

        GiteaAccount.objects.create(user=self.ada, username="ada")
        report = plan(self.ada)
        self.assertEqual(report["deleted"]["gitea.GiteaAccount"], 1)
        self.assertTrue(any("Gitea" in note for note in report["notes"]))

    @unittest.skipUnless(apps.is_installed("toto.gitea"), "no forge on this host")
    def test_no_forge_note_without_a_forge_account(self):
        self.assertFalse(any("Gitea" in note for note in plan(self.ada)["notes"]))

    def test_files_in_a_remote_bucket_bring_their_own_note(self):
        from toto.vault.models import Bucket, VaultFile

        bucket = Bucket.objects.create(name="Far", slug="far", owner=self.ada,
                                       storage_backend="s3")
        VaultFile.objects.create(owner=self.ada, title="far.txt", key="far",
                                 file_type="text", bucket=bucket)
        notes = plan(self.ada)["notes"]
        self.assertTrue(any(note.startswith("1 file(s) live in a remote bucket") for note in notes))

    def test_a_bucket_they_owned_stays_ownerless_and_the_report_says_so(self):
        # Bucket.owner is SET_NULL since 2026-09-30: a bucket may hold other
        # people's files, so erasing its owner detaches it and deletes nothing.
        from toto.vault.models import Bucket

        bucket = Bucket.objects.create(name="Shared", slug="shared", owner=self.ada)
        report = plan(self.ada)
        self.assertEqual(report["detached"].get("vault.Bucket.owner"), 1)
        self.assertTrue(any(note.startswith("1 bucket(s) they owned stay") for note in report["notes"]))
        self.ada.delete()
        bucket.refresh_from_db()
        self.assertIsNone(bucket.owner_id)

    def test_files_on_this_disk_bring_no_remote_note(self):
        from toto.vault.models import Bucket, VaultFile

        bucket = Bucket.objects.create(name="Near", slug="near", owner=self.ada)
        VaultFile.objects.create(owner=self.ada, title="near.txt", key="near",
                                 file_type="text", bucket=bucket)
        self.assertFalse(any("remote bucket" in note for note in plan(self.ada)["notes"]))

    @unittest.skipUnless(apps.is_installed("wakawaka"), "no wiki on this host")
    def test_a_page_only_they_wrote_is_counted_among_the_deleted(self):
        from wakawaka.models import Revision, WikiPage

        own = WikiPage.objects.create(slug="AdaOnly")
        Revision.objects.create(page=own, content="mine", creator=self.ada)
        shared = WikiPage.objects.create(slug="Shared")
        Revision.objects.create(page=shared, content="a", creator=self.ada)
        Revision.objects.create(page=shared, content="b", creator=self.root)
        WikiPage.objects.create(slug="NeverWritten")
        self.assertEqual(plan(self.ada)["deleted"]["wakawaka.WikiPage"], 1)

    @unittest.skipUnless(apps.is_installed("wakawaka"), "no wiki on this host")
    def test_an_empty_page_is_not_theirs_to_take(self):
        from wakawaka.models import WikiPage

        WikiPage.objects.create(slug="NeverWritten")
        run("erase_user", "ada", "--confirm", "ada")
        self.assertTrue(WikiPage.objects.filter(slug="NeverWritten").exists())


class BlockedTests(EraseCase):
    def _blocked_by(self, error_class):
        blocker = Comment(pk=1, body="x")
        exc = error_class("cannot", {blocker, Comment(pk=2, body="y")})
        return mock.patch.object(Collector, "collect", side_effect=exc)

    def test_a_protected_row_is_named_and_the_erase_refused(self):
        with self._blocked_by(ProtectedError):
            code, out, _ = run("erase_user", "ada", "--confirm", "ada")
        self.assertEqual(code, 1)
        self.assertIn("may not be deleted", out["error"])
        self.assertEqual(out["report"]["blocked_by"], ["comments.Comment"])
        self.assertTrue(User.objects.filter(username="ada").exists())

    def test_a_restricted_row_blocks_the_report_too(self):
        with self._blocked_by(RestrictedError):
            code, out, _ = run("erase_user", "ada")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["report"]["blocked_by"], ["comments.Comment"])

    @unittest.skip("SUSPECTED BUG toto/core/management/commands/erase_user.py:63 - an "
                   "account whose gervazy strongbox holds a data key is blocked_by "
                   "gervazy.WrappedDataKey (PROTECT on vmk), although both rows are "
                   "theirs and in the same cascade: anyone who ever encrypted cannot "
                   "be erased.")
    def test_an_account_with_its_own_encryption_keys_can_be_erased(self):
        from toto.gervazy.models import UserStrongbox, VaultMasterKey, WrappedDataKey

        box = UserStrongbox.objects.create(owner=self.ada)
        vmk = VaultMasterKey.objects.create(strongbox=box, encrypted_vmk=b"k")
        WrappedDataKey.objects.create(strongbox=box, vmk=vmk, encrypted_dek=b"d")
        code, out, _ = run("erase_user", "ada", "--confirm", "ada")
        self.assertEqual(code, 0, out)
        self.assertFalse(UserStrongbox.objects.filter(pk=box.pk).exists())


class SuperuserGuardTests(EraseCase):
    def test_the_last_active_superuser_is_refused_even_a_report_with_its_report(self):
        code, out, _ = run("erase_user", "root")
        self.assertEqual(code, 1)
        self.assertIn("last active superuser", out["error"])
        self.assertEqual(out["report"]["username"], "root")

    def test_an_inactive_second_superuser_does_not_count_as_another(self):
        User.objects.create_superuser("dormant", "d@example.com", "pw", is_active=False)
        code, out, _ = run("erase_user", "root", "--confirm", "root")
        self.assertEqual(code, 1)
        self.assertTrue(User.objects.filter(username="root").exists())

    def test_with_another_active_superuser_a_superuser_can_go(self):
        User.objects.create_superuser("second", "s@example.com", "pw")
        code, out, _ = run("erase_user", "root", "--confirm", "root")
        self.assertEqual(code, 0, out)
        self.assertFalse(User.objects.filter(username="root").exists())

    def test_a_deactivated_superuser_is_not_guarding_anything(self):
        self.root.is_active = False
        self.root.save()
        code, out, _ = run("erase_user", "root", "--confirm", "root")
        self.assertEqual(code, 0, out)


class ErasedTests(EraseCase):
    def test_a_comment_they_wrote_stays_in_its_thread_without_them(self):
        comment = comments.add(self.ada, "still useful")
        run("erase_user", "ada", "--confirm", "ada")
        comment.refresh_from_db()
        self.assertIsNone(comment.author_id)
        self.assertEqual(comment.body, "still useful")

    @unittest.skipUnless(apps.is_installed("toto.assets"), "no ledger on this host")
    def test_their_ledger_account_stays_with_its_history_detached(self):
        from toto.assets.models import LedgerAccount

        accounts = list(LedgerAccount.objects.filter(user=self.ada).values_list("pk", flat=True))
        if not accounts:
            self.skipTest("this host opens no ledger account per member")
        report = plan(self.ada)
        self.assertEqual(report["detached"].get("assets.LedgerAccount.user", 0), len(accounts))
        run("erase_user", "ada", "--confirm", "ada")
        self.assertEqual(LedgerAccount.objects.filter(pk__in=accounts, user__isnull=True).count(),
                         len(accounts))

    def test_the_erasure_is_on_the_chain_as_the_system_s_act(self):
        comments.add(self.ada, "x")
        code, out, _ = run("erase_user", "ada", "--confirm", "ada")
        erased = AuditRecord.objects.get(action="AUTH.ACCOUNT_ERASED")
        self.assertIsNone(erased.actor_user_id)
        self.assertEqual(erased.object_id, str(out["report"]["id"]))
        self.assertEqual(erased.metadata["deleted"]["auth.User"], 1)
        self.assertEqual(erased.metadata["detached"]["comments.Comment.author"], 1)
        self.assertTrue(verify_chain().ok)

    def test_the_erase_stands_when_the_chain_cannot_record_it(self):
        with mock.patch("toto.audit.services.record", side_effect=RuntimeError("down")):
            code, out, err = run("erase_user", "ada", "--confirm", "ada")
        self.assertEqual(code, 0)
        self.assertTrue(out["erased"])
        self.assertIn("audit record could not be written", err)
        self.assertFalse(User.objects.filter(username="ada").exists())

    def test_their_earlier_records_still_verify_after_the_erase(self):
        record("DOC_EDITED", app_label="t", actor_user=self.ada)
        run("erase_user", "ada", "--confirm", "ada")
        kept = AuditRecord.objects.get(action="DOC_EDITED")
        self.assertEqual(kept.actor_username, "ada")
        self.assertTrue(verify_chain().ok)

    def test_an_unknown_account_is_named_in_the_refusal(self):
        code, out, _ = run("erase_user", "nobody")
        self.assertEqual(code, 1)
        self.assertEqual(out, {"ok": False, "error": "there is no account called 'nobody'"})

    def test_the_answer_is_one_line_of_json_with_sorted_keys(self):
        out = io.StringIO()
        call_command("erase_user", "ada", stdout=out)
        line = out.getvalue().strip()
        self.assertEqual(len(line.splitlines()), 1)
        self.assertEqual(line, json.dumps(json.loads(line), sort_keys=True))

    @unittest.skip("SUSPECTED BUG toto/core/management/commands/erase_user.py:67-73 - plan() "
                   "lists every related model with a count of 0 (about 90 lines on this "
                   "host), so the console report buries what actually goes or stays.")
    def test_the_report_lists_only_what_is_actually_there(self):
        report = plan(self.ada)
        self.assertEqual([k for k, n in report["deleted"].items() if not n], [])
        self.assertEqual([k for k, n in report["detached"].items() if not n], [])
