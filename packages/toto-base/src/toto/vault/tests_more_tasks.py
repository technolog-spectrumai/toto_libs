"""The ``vault-encrypt`` worker task and the vault's predefined workflow
steps: the run is closed with a reason on every failure, the password never
lands in a row, and a step started the wrong way fails loudly.
"""

import json
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from toto.gervazy.models import UserStrongbox
from toto.vault import predefined_tasks
from toto.vault.models import VaultFile
from toto.vault.tasks import _pdf_friendly_error, encrypt_workflow_run
from toto.vault.views import EncryptFileView

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="vault-more-tasks-"))
class EncryptRunTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user("owner", password="pw")

    def run_for(self, file_pk):
        from toto.workflows.models import WorkflowRun

        return WorkflowRun.objects.create(
            workflow=EncryptFileView._ensure_workflow(),
            input_data={"data": {"file_pk": file_pk, "owner_id": self.owner.pk}},
            started_by=self.owner)

    def file(self, *, file_type="text", body=b"words", title="t.txt"):
        vault_file = VaultFile(owner=self.owner, title=title, file_type=file_type, is_public=True)
        vault_file.file.save(title, ContentFile(body), save=False)
        vault_file.save()
        return vault_file

    def test_a_vanished_run_is_reported_not_raised(self):
        self.assertEqual(encrypt_workflow_run(424242, "pw"),
                         {"ok": False, "error": "Workflow run not found."})

    def test_a_vanished_file_fails_the_run_and_its_node(self):
        from toto.workflows.models import WorkflowRun

        run = self.run_for(987654)
        self.assertEqual(encrypt_workflow_run(run.pk, "pw")["error"], "File not found.")
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertEqual(run.output_data, {"ok": False, "error": "File not found."})
        node_run = run.node_runs.get()
        self.assertEqual(node_run.error, "File not found.")
        self.assertIsNotNone(node_run.completed_at)

    def test_an_encrypted_file_is_not_encrypted_twice(self):
        f = self.file()
        VaultFile.objects.filter(pk=f.pk).update(is_encrypted=True)
        run = self.run_for(f.pk)
        self.assertEqual(encrypt_workflow_run(run.pk, "pw")["error"], "File is already encrypted.")

    def test_a_broken_pdf_is_named_in_words_a_person_understands(self):
        f = self.file(file_type="pdf", body=b"not a pdf at all", title="bad.pdf")
        run = self.run_for(f.pk)
        result = encrypt_workflow_run(run.pk, "pw")
        self.assertEqual(result, {"ok": False, "error": "File does not appear to be a valid PDF."})

    def test_a_finished_run_carries_the_url_and_never_the_password(self):
        from toto.workflows.models import WorkflowRun

        UserStrongbox.objects.create(owner=self.owner, name="k", argon2_memory_cost=19456,
                                     argon2_iterations=2, argon2_lanes=1)
        f = self.file()
        run = self.run_for(f.pk)
        result = encrypt_workflow_run(run.pk, "hunter2-secret")
        self.assertTrue(result["ok"])
        run.refresh_from_db()
        f.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertTrue(f.is_encrypted)
        self.assertFalse(f.is_public)
        self.assertEqual(run.output_data["vault_file_id"], f.pk)
        stored = json.dumps([run.input_data, run.output_data] + [
            [n.input_data, n.output_data] for n in run.node_runs.all()])
        self.assertNotIn("hunter2-secret", stored)

    def test_the_friendly_error_leaves_other_messages_alone(self):
        self.assertEqual(_pdf_friendly_error(ValueError("disk full")), "disk full")
        self.assertEqual(_pdf_friendly_error(ValueError("EOF marker not found")),
                         "File does not appear to be a valid PDF.")


class PredefinedStepTests(TestCase):
    def test_the_encrypt_node_refuses_to_run_without_its_password_path(self):
        with self.assertRaisesMessage(RuntimeError, "encrypt_workflow_run"):
            predefined_tasks.vault_encrypt_file({"data": {"file_pk": 1}})

    def test_the_refresh_and_transfer_steps_need_a_run_id(self):
        for step in (predefined_tasks.vault_refresh_remote_bucket,
                     predefined_tasks.vault_transfer_files):
            with self.assertRaisesMessage(ValueError, "requires run_id"):
                step({"data": {}})
            with self.assertRaisesMessage(ValueError, "requires run_id"):
                step({})
