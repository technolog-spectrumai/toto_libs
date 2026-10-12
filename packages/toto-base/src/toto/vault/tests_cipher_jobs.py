"""Encryption and decryption off the request (``cipher_jobs``, 2026-10-10).

A host with ``VAULT_ENCRYPT_WORKFLOW_MB`` set never encrypts or decrypts in
the request: the worker does, as a workflow run above the limit and a plain
task up to it; without a worker the door refuses. A host without the setting
keeps the old way.
"""
import json
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.vault import cipher_jobs
from toto.vault.models import Bucket, VaultFile
from toto.vault.tasks import cipher_file_task

PW = "hunter2-super-secret"


class _Fixture(TestCase):
    limit = 50

    @classmethod
    def setUpClass(cls):
        cls.temp_media = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_media, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp_media, VAULT_ROOT=self.temp_media,
                                           VAULT_ENCRYPT_WORKFLOW_MB=self.limit)
        self._override.enable()
        cache.clear()
        Platform.objects.create(site_name="Test", author="Test", publication_year=2026, active=True)
        self.alice = User.objects.create_user("cj_alice", password="pass")
        self.bob = User.objects.create_user("cj_bob", password="pass")
        self.client = Client()
        self.client.force_login(self.alice)
        self.bucket = Bucket.objects.create(name="CJ", slug="cj-bucket", owner=self.alice)
        self.f = VaultFile.objects.create(
            owner=self.alice, title="secret.txt", key="cj-secret",
            file=SimpleUploadedFile("secret.txt", b"hello"), file_type="text",
            bucket=self.bucket, is_public=True)

    def tearDown(self):
        self._override.disable()

    def _workflows_or_skip(self):
        from django.apps import apps

        if not apps.is_installed("toto.workflows"):
            self.skipTest("workflows not installed")

    def encrypt(self, **extra):
        return self.client.post(reverse("vault:encrypt_file"),
                                {"file_pk": self.f.pk, "password": PW, **extra})

    def decrypt(self):
        return self.client.post(reverse("vault:decrypt_file"), {"file_pk": self.f.pk, "password": PW})


class UnderTheLimitTests(_Fixture):
    def test_a_small_file_is_a_plain_worker_task_and_no_run(self):
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=True), \
             patch("toto.vault.tasks.cipher_file_task") as task:
            answer = self.encrypt()
        self.assertEqual(answer.status_code, 200)
        data = answer.json()
        self.assertTrue(data["ok"] and data["async"])
        self.assertNotIn("workflow_run_id", data)
        task.delay.assert_called_once_with(data["job_id"], self.f.pk, "encrypt", PW, None, False)
        self.f.refresh_from_db()
        self.assertFalse(self.f.is_encrypted)           # nothing in the request
        from django.apps import apps

        if apps.is_installed("toto.workflows"):
            from toto.workflows.models import WorkflowRun

            self.assertFalse(WorkflowRun.objects.exists())

    def test_decryption_goes_to_the_worker_too(self):
        VaultFile.objects.filter(pk=self.f.pk).update(is_encrypted=True)
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=True), \
             patch("toto.vault.tasks.cipher_file_task") as task:
            data = self.decrypt().json()
        task.delay.assert_called_once_with(data["job_id"], self.f.pk, "decrypt", PW, None, True)

    def test_no_worker_no_fallback(self):
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=False), \
             patch("toto.vault.tasks.cipher_file_task") as task:
            answer = self.encrypt()
        self.assertEqual(answer.status_code, 503)
        self.assertFalse(answer.json()["ok"])
        task.delay.assert_not_called()
        self.f.refresh_from_db()
        self.assertFalse(self.f.is_encrypted)

    def test_the_job_is_its_owners_and_ends_with_the_workers_word(self):
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=True), \
             patch("toto.vault.tasks.cipher_file_task"):
            job_id = self.encrypt().json()["job_id"]
        url = reverse("vault:encrypt_status")
        running = self.client.get(url, {"job_id": job_id}).json()
        self.assertEqual((running["done"], running["is_terminal"]), (False, False))
        other = Client()
        other.force_login(self.bob)
        self.assertEqual(other.get(url, {"job_id": job_id}).status_code, 404)
        self.assertEqual(self.client.get(url, {"job_id": "no-such-job"}).status_code, 404)
        # The task itself, with the act stubbed: its word reaches the page.
        with patch("toto.vault.cipher_jobs.perform",
                   return_value={"vault_file_id": self.f.pk, "raw_url": "/x/"}) as act:
            cipher_file_task(job_id, self.f.pk, "encrypt", PW)
        act.assert_called_once()
        done = self.client.get(url, {"job_id": job_id}).json()
        self.assertEqual((done["done"], done["ok"], done["raw_url"]), (True, True, "/x/"))
        self.assertNotIn(PW, json.dumps(done))

    def test_a_failure_is_the_jobs_answer_not_an_exception(self):
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=True), \
             patch("toto.vault.tasks.cipher_file_task"):
            job_id = self.encrypt().json()["job_id"]
        with patch("toto.vault.cipher_jobs.perform", side_effect=ValueError("no key store")):
            result = cipher_file_task(job_id, self.f.pk, "encrypt", PW)
        self.assertEqual(result, {"ok": False, "error": "no key store"})
        done = self.client.get(reverse("vault:encrypt_status"), {"job_id": job_id}).json()
        self.assertEqual((done["done"], done["ok"], done["error"]), (True, False, "no key store"))


class OverTheLimitTests(_Fixture):
    limit = 0           # every file is larger than nothing

    def test_a_file_over_the_limit_is_a_workflow_run(self):
        self._workflows_or_skip()
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=True), \
             patch("toto.vault.tasks.encrypt_workflow_run") as task, \
             patch("toto.vault.tasks.cipher_file_task") as plain:
            data = self.encrypt().json()
        plain.delay.assert_not_called()
        task.delay.assert_called_once_with(data["workflow_run_id"], PW, None)
        from toto.workflows.models import WorkflowRun

        run = WorkflowRun.objects.get(pk=data["workflow_run_id"])
        self.assertEqual(run.workflow.slug, "vault-encrypt")
        self.assertEqual(run.started_by, self.alice)
        self.assertNotIn(PW, json.dumps(run.input_data))

    def test_so_is_a_decryption(self):
        self._workflows_or_skip()
        VaultFile.objects.filter(pk=self.f.pk).update(is_encrypted=True)
        with patch("toto.vault.cipher_jobs.worker_ready", return_value=True), \
             patch("toto.vault.tasks.decrypt_workflow_run") as task:
            data = self.decrypt().json()
        task.delay.assert_called_once_with(data["workflow_run_id"], PW)
        from toto.workflows.models import WorkflowRun

        run = WorkflowRun.objects.get(pk=data["workflow_run_id"])
        self.assertEqual(run.workflow.slug, "vault-decrypt")
        self.assertEqual(run.input_data["data"], {"file_pk": self.f.pk, "owner_id": self.alice.pk,
                                                  "publish": True})

    def test_the_decrypt_run_ends_with_the_files_address(self):
        self._workflows_or_skip()
        from toto.vault.tasks import decrypt_workflow_run
        from toto.vault.workflow import ensure_decrypt_workflow
        from toto.workflows.models import WorkflowRun

        VaultFile.objects.filter(pk=self.f.pk).update(is_encrypted=True)
        run = WorkflowRun.objects.create(
            workflow=ensure_decrypt_workflow(), started_by=self.alice,
            input_data={"data": {"file_pk": self.f.pk, "owner_id": self.alice.pk}})
        with patch("toto.vault.cipher_jobs.perform",
                   return_value={"vault_file_id": self.f.pk, "url": "/y/"}):
            self.assertEqual(decrypt_workflow_run(run.pk, PW), {"ok": True, "url": "/y/"})
        status = self.client.get(reverse("vault:encrypt_status"), {"run_id": run.pk}).json()
        self.assertEqual((status["done"], status["ok"], status["url"]), (True, True, "/y/"))


@override_settings(VAULT_ENCRYPT_WORKFLOW_MB=None)
class WithoutTheSettingTests(_Fixture):
    limit = None

    def test_the_old_way_stands(self):
        self.assertFalse(cipher_jobs.off_request())
        with patch("toto.vault.tasks.cipher_file_task") as task, \
             patch.object(VaultFile, "encrypt") as act:
            answer = self.encrypt()
        task.delay.assert_not_called()
        act.assert_called_once()
        self.assertNotIn("job_id", answer.json())


class LimitTests(TestCase):
    def test_the_limit_reads_megabytes_and_nothing_else(self):
        for raw, want in ((None, None), ("", None), ("x", None), (0, 0), ("16", 16), (-3, 0)):
            with override_settings(VAULT_ENCRYPT_WORKFLOW_MB=raw):
                self.assertEqual(cipher_jobs.limit_mb(), want, raw)
