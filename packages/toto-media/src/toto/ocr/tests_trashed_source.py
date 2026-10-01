"""A scan read from a vault file that is now in the trash (2026-10-01).

The run's foreign key still finds a trashed file — the vault's base manager
sees the trash — so a retry read its bytes as if nothing had happened. A
trashed source is now a missing one: ``runs.source_path`` refuses it, the
retry door says why with a sentence, the page shows that sentence instead of
a button that could only be refused, and a page task fails at once. Restored,
the file is read again.

    manage.py test toto.ocr.tests_trashed_source --settings=toto.ocr.testing.settings
"""

from __future__ import annotations

import io
import tempfile
from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.core.models import Platform
from toto.ocr import runs
from toto.ocr.models import OcrPage, OcrRun, PageStatus, RunStatus
from toto.ocr.validation import Inspection

TRASH_SENTENCE = "is in the trash"


def png_bytes():
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (30, 20), 255).save(buf, format="PNG")
    return buf.getvalue()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="ocr-trashed-"))
class TrashedSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.vault.models import Bucket

        Platform.objects.create(site_name="Test", author="t", publication_year=2026,
                                active=True)
        cls.user = User.objects.create_user("reader", password="x")
        cls.bucket = Bucket.objects.create(name="Scans", slug="scans", owner=cls.user)

    def setUp(self):
        from toto.vault.models import VaultFile

        self.client.force_login(self.user)
        self.vault_file = VaultFile.objects.create(
            owner=self.user, title="scan.png", key="scan", file_type="image",
            bucket=self.bucket, file=SimpleUploadedFile("scan.png", png_bytes()))
        run = runs.create_run(owner=self.user,
                              inspection=Inspection(ok=True, kind="image", page_count=1),
                              language="eng", source_name="scan.png",
                              vault_file=self.vault_file)
        run.pages.update(status=PageStatus.FAILED)
        OcrRun.objects.filter(pk=run.pk).update(status=RunStatus.FAILED, pages_settled=1,
                                                pages_failed=1)
        self.run = OcrRun.objects.get(pk=run.pk)

    def _trash(self):
        self.vault_file.trash(self.user)
        self.run = OcrRun.objects.get(pk=self.run.pk)

    def _retry(self):
        with mock.patch("toto.ocr.tasks.ocr_page.apply_async",
                        return_value=mock.Mock(id="task-1")) as queued:
            response = self.client.post(reverse("ocr:retry", args=[self.run.pk]))
        return response, queued

    def test_source_path_reads_a_live_file(self):
        self.assertEqual(runs.source_path(self.run), self.vault_file.file.path)
        self.assertFalse(runs.source_trashed(self.run))

    def test_source_path_refuses_a_trashed_file(self):
        self._trash()
        self.assertTrue(runs.source_trashed(self.run))
        with self.assertRaises(runs.SourceTrashed):
            runs.source_path(self.run)
        # A missing file, to every caller that already handles one.
        self.assertTrue(issubclass(runs.SourceTrashed, FileNotFoundError))

    def test_the_retry_is_refused_with_a_sentence(self):
        self._trash()
        response, queued = self._retry()
        self.assertEqual(response.status_code, 409)
        self.assertIn(TRASH_SENTENCE, response.json()["error"])
        self.assertFalse(queued.called)
        self.assertEqual(OcrPage.objects.get(run=self.run).status, PageStatus.FAILED)
        self.assertEqual(OcrRun.objects.get(pk=self.run.pk).status, RunStatus.FAILED)

    def test_the_page_says_why_instead_of_offering_the_button(self):
        self._trash()
        response = self.client.get(reverse("ocr:run", args=[self.run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_retry"])
        self.assertIn(TRASH_SENTENCE, response.context["retry_refusal"])
        body = response.content.decode()
        self.assertIn(TRASH_SENTENCE, body)
        self.assertNotIn(reverse("ocr:retry", args=[self.run.pk]), body)

    def test_a_restored_file_is_read_again(self):
        from toto.vault.models import VaultFile
        from toto.vault.trash import restore_file

        self._trash()
        restore_file(VaultFile.all_objects.get(pk=self.vault_file.pk), by=self.user)
        page = self.client.get(reverse("ocr:run", args=[self.run.pk]))
        self.assertTrue(page.context["can_retry"])
        self.assertEqual(page.context["retry_refusal"], "")
        response, queued = self._retry()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["retried"], 1)
        self.assertTrue(queued.called)

    def test_a_page_task_fails_at_once_without_reading(self):
        from toto.ocr import engine
        from toto.ocr.tasks import ocr_page

        OcrPage.objects.filter(run=self.run).update(status=PageStatus.WAITING)
        OcrRun.objects.filter(pk=self.run.pk).update(
            status=RunStatus.RUNNING, finished_at=None, pages_settled=0, pages_failed=0)
        self._trash()
        with mock.patch.object(engine, "read_image") as read:
            self.assertEqual(ocr_page(self.run.pk, 1), "failed")
        self.assertFalse(read.called)
        page = OcrPage.objects.get(run=self.run)
        self.assertEqual(page.status, PageStatus.FAILED)
        self.assertIn("trash", page.error)
        self.assertEqual(OcrRun.objects.get(pk=self.run.pk).status, RunStatus.FAILED)
