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
