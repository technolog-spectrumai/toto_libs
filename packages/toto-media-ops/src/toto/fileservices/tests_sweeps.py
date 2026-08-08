"""Fileservices' closer, the fixed dispatch gap, and its sweep policy.

Named tests_sweeps.py (sibling of tests.py) and listed explicitly in the
gate — `toto` is a namespace package, discovery finds nothing by itself.
"""

import datetime

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone

from toto.quota import sweeps

from .dispatch import create_service_run, fail_run
from .models import FileServiceRun

User = get_user_model()


class FileservicesSweepTests(TestCase):
    def setUp(self):
        import shutil
        import tempfile

        from django.test import override_settings

        from toto.vault.models import Bucket, VaultFile

        # The deployed MEDIA_ROOT is a root-owned bind mount; every test that
        # writes a real file gets its own tempdir (the ambrosia base's shape).
        media = tempfile.mkdtemp(prefix="fileservices-test-")
        override = override_settings(MEDIA_ROOT=media)
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        self.owner = User.objects.create_user("alice", password="pw")
        self.bucket = Bucket.objects.create(name="b", slug="b", owner=self.owner)
        self.vf = VaultFile.objects.create(
            owner=self.owner, title="in.txt", file_type="text",
            file=SimpleUploadedFile("in.txt", b"x"), bucket=self.bucket)

    def _age(self, run, hours):
        FileServiceRun.objects.filter(pk=run.pk).update(
            started_at=timezone.now() - datetime.timedelta(hours=hours))
        run.refresh_from_db()
        return run

    def test_fail_run_appends_and_closes(self):
        run = create_service_run(self.owner, self.vf, "svc")

        fail_run(run, "Could not be queued: broker down")

        run.refresh_from_db()
        self.assertEqual(run.status, FileServiceRun.FAILED)
        self.assertIn("broker down", run.stderr)
        self.assertIsNotNone(run.finished_at)

    def test_sweep_closes_the_stuck_pending_row(self):
        stuck = self._age(create_service_run(self.owner, self.vf, "svc"), 4)
        fresh = create_service_run(self.owner, self.vf, "svc")

        closed = sweeps.run_sweeps()

        self.assertEqual(closed.get("fileservices.FileServiceRun"), 1)
        stuck.refresh_from_db()
        self.assertEqual(stuck.status, FileServiceRun.FAILED)
        self.assertIn("stuck-run sweeper", stuck.stderr)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, FileServiceRun.PENDING)

    def test_direct_task_is_worker_discoverable(self):
        # Review finding [critical]: the extended-runtime path enqueues
        # run_file_service_task; without this registry entry the worker
        # answers KeyError and a paying user's run never executes.
        from toto.registry import TASK_MODULES

        self.assertIn("toto.fileservices", TASK_MODULES)

    def test_never_ran_close_refunds(self):
        from unittest.mock import patch

        run = create_service_run(self.owner, self.vf, "svc")
        with patch("toto.quota.charge.refund_for") as refund:
            fail_run(run, "swept")
        refund.assert_called_once()
        self.assertEqual(refund.call_args.args[:3],
                         ("fileservices.FileServiceRun", run.pk, "fileservices.run"))

    def test_terminal_row_is_not_resurrected_by_a_redelivered_task(self):
        from .runner import execute_run

        run = create_service_run(self.owner, self.vf, "svc")
        fail_run(run, "swept")

        result = execute_run(run.id)

        self.assertTrue(result.get("skipped"))
        run.refresh_from_db()
        self.assertEqual(run.status, FileServiceRun.FAILED)
