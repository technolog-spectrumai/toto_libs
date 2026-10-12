"""The nightly trash cleanup as a workflow run (2026-10-11): the Celery task
``purge_expired_trash`` runs the ``vault-trash-purge`` workflow where the
engine is installed, and purges directly where it is not or cannot.

    manage.py test toto.vault.tests_trash_workflow
"""
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.core.models import Platform
from toto.vault.models import Bucket, VaultFile
from toto.vault.tasks import purge_expired_trash


class TrashCleanupTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.mkdtemp()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self._override = override_settings(MEDIA_ROOT=self.temp, VAULT_ROOT=self.temp,
                                           VAULT_TRASH_DAYS=30)
        self._override.enable()
        Platform.objects.get_or_create(active=True, defaults={
            "site_name": "Test", "author": "t", "publication_year": 2026})
        owner = User.objects.create_user("tc_owner", password="pw")
        bucket = Bucket.objects.create(name="TC", slug="tc-bucket", owner=owner)

        def trashed(key, days):
            row = VaultFile.objects.create(
                owner=owner, title=f"{key}.txt", key=key, bucket=bucket, file_type="text",
                file=SimpleUploadedFile(f"{key}.txt", b"x"))
            VaultFile.all_objects.filter(pk=row.pk).update(
                trashed_at=timezone.now() - timedelta(days=days))
            return row.pk

        self.old, self.fresh = trashed("tc-old", 31), trashed("tc-fresh", 2)

    def tearDown(self):
        self._override.disable()

    def left(self):
        return set(VaultFile.all_objects.values_list("pk", flat=True))

    def test_the_night_is_a_run_with_its_counts(self):
        if not apps.is_installed("toto.workflows"):
            self.skipTest("workflows not installed")
        from toto.workflows.models import WorkflowRun

        result = purge_expired_trash()
        self.assertEqual(self.left(), {self.fresh})
        run = WorkflowRun.objects.get(pk=result["workflow_run_id"])
        self.assertEqual((run.workflow.slug, run.status, run.started_by),
                         ("vault-trash-purge", WorkflowRun.COMPLETED, None))
        self.assertEqual((result["purged"], run.output_data["purged"]), (1, 1))
        # A second night finds nothing due, and is a run all the same.
        again = purge_expired_trash()
        self.assertEqual(again["purged"], 0)
        self.assertEqual(WorkflowRun.objects.count(), 2)

    def test_a_cleanup_is_never_skipped_for_the_sake_of_its_record(self):
        with mock.patch("toto.vault.tasks._purge_trash_as_workflow",
                        side_effect=RuntimeError("engine down")):
            result = purge_expired_trash()
        self.assertEqual(result["purged"], 1)
        self.assertNotIn("workflow_run_id", result)
        self.assertEqual(self.left(), {self.fresh})
