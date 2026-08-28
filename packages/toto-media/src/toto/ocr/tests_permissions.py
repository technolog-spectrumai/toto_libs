"""Who may do what — and the metering ladder at the door.

The old app was superuser-only, which is not what an Office application is.
Every route is now open to any signed-in member for their own work, and a run
belonging to somebody else is NOT FOUND rather than forbidden: a refusal that
distinguishes the two tells a stranger the document exists.
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
from toto.ocr.models import OcrRun, RunStatus
from toto.ocr.validation import Inspection

MEDIA = tempfile.mkdtemp(prefix="ocr-perm-")


def png_upload(name="scan.png"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("L", (30, 20), 255).save(buf, format="PNG")
    return SimpleUploadedFile(name, buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA)
class _Fixture(TestCase):
    @classmethod
    def setUpTestData(cls):
        Platform.objects.create(site_name="Test", author="t",
                                publication_year=2026, active=True)
        cls.user = User.objects.create_user("reader", password="x")
        cls.other = User.objects.create_user("stranger", password="x")
        cls.staff = User.objects.create_user("keeper", password="x",
                                             is_staff=True)

    def setUp(self):
        self.client.force_login(self.user)

    def a_run(self, owner=None, status=RunStatus.SUCCESS, text="some text"):
        run = runs.create_run(
            owner=owner or self.user,
            inspection=Inspection(ok=True, kind="image", page_count=1),
            language="eng", source_name="scan.png", uploaded=png_upload())
        OcrRun.objects.filter(pk=run.pk).update(status=status, text=text)
        run.refresh_from_db()
        return run


class OwnershipTests(_Fixture):
    def test_a_stranger_gets_not_found_never_forbidden(self):
        run = self.a_run()
        self.client.force_login(self.other)
        for name in ("ocr:run", "ocr:status", "ocr:text"):
            with self.subTest(route=name):
                response = self.client.get(reverse(name, args=[run.pk]))
                self.assertEqual(response.status_code, 404)

    def test_the_owner_may_read_their_own(self):
        run = self.a_run()
        for name in ("ocr:run", "ocr:status", "ocr:text"):
            with self.subTest(route=name):
                self.assertEqual(
                    self.client.get(reverse(name, args=[run.pk])).status_code, 200)

    def test_staff_may_look(self):
        run = self.a_run()
        self.client.force_login(self.staff)
        self.assertEqual(
            self.client.get(reverse("ocr:status", args=[run.pk])).status_code, 200)

    def test_an_ordinary_member_may_use_the_tool(self):
        """It was superuser-only. An Office application is not."""
        self.assertEqual(self.client.get(reverse("ocr:home")).status_code, 200)

    def test_the_text_download_is_a_get(self):
        """So a lapsed plan never traps work that was already paid for."""
        run = self.a_run(text="hello there")
        response = self.client.get(reverse("ocr:text", args=[run.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertEqual(response.content.decode(), "hello there")


class SubmitLadderTests(_Fixture):
    def _submit(self, **extra):
        data = {"document": png_upload(), "language": "eng"}
        data.update(extra)
        return self.client.post(reverse("ocr:submit"), data)

    def test_a_language_this_server_lacks_is_refused_by_name(self):
        from toto.ocr import engine

        with mock.patch.object(engine, "tesseract_available", return_value=True), \
             mock.patch.object(engine, "available_languages", return_value=["eng"]), \
             mock.patch.object(engine, "is_offered", return_value=False):
            response = self._submit(language="pol")
        self.assertEqual(response.status_code, 400)
        self.assertIn("eng", response.json()["error"])

    def test_no_tesseract_is_a_503_not_a_500(self):
        from toto.ocr import engine

        with mock.patch.object(engine, "tesseract_available", return_value=False):
            self.assertEqual(self._submit().status_code, 503)

    def test_a_second_run_while_one_is_reading_is_refused(self):
        """One job at a time, per person: this platform runs every background
        job on ONE queue."""
        from toto.ocr import engine

        self.a_run(status=RunStatus.RUNNING)
        with mock.patch.object(engine, "tesseract_available", return_value=True), \
             mock.patch.object(engine, "is_offered", return_value=True):
            response = self._submit()
        self.assertEqual(response.status_code, 409)

    def test_an_oversized_upload_is_413_before_a_row_exists(self):
        from toto.ocr import engine
        from toto.ocr.models import OcrSettings

        row = OcrSettings.get()
        row.max_upload_mb = 1
        row.save()
        big = SimpleUploadedFile("big.png", b"x" * (2 * 1024 * 1024),
                                 content_type="image/png")
        with mock.patch.object(engine, "tesseract_available", return_value=True), \
             mock.patch.object(engine, "is_offered", return_value=True):
            response = self.client.post(reverse("ocr:submit"),
                                        {"document": big, "language": "eng"})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(OcrRun.objects.filter(owner=self.user).exists())

    def test_being_over_quota_refuses_before_any_bytes_are_stored(self):
        """Money and plan are decided once, for the whole job, before any work
        happens — refusing halfway would waste what was already done."""
        from toto.ocr import engine
        from toto.ocr.models import OcrQuotaPolicy
        from toto.quota import QuotaExceeded

        policy = OcrQuotaPolicy(metric_code="ocr.page", name="Page read",
                                unit="page")
        with mock.patch.object(engine, "tesseract_available", return_value=True), \
             mock.patch.object(engine, "is_offered", return_value=True), \
             mock.patch("toto.quota.check_quota",
                        side_effect=QuotaExceeded(policy, 500, 500)):
            response = self._submit()
        self.assertIn(response.status_code, (402, 429))
        self.assertFalse(OcrRun.objects.filter(owner=self.user).exists())

    def test_no_worker_is_503_and_the_run_says_why(self):
        from toto.ocr import dispatch, engine

        with mock.patch.object(engine, "tesseract_available", return_value=True), \
             mock.patch.object(engine, "is_offered", return_value=True), \
             mock.patch.object(dispatch, "celery_available", return_value=False):
            response = self._submit()
        self.assertEqual(response.status_code, 503)
        run = OcrRun.objects.get(pk=response.json()["run_id"])
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn("worker", run.error)


class SaveTests(_Fixture):
    def test_saving_needs_a_folder_you_own(self):
        from toto.vault.models import Bucket

        run = self.a_run()
        theirs = Bucket.objects.create(name="theirs", owner=self.other)
        response = self.client.post(reverse("ocr:save", args=[run.pk]),
                                    {"bucket": theirs.pk})
        self.assertEqual(response.status_code, 400)

    def test_saving_writes_a_text_file_into_the_vault(self):
        from toto.vault.models import Bucket, VaultFile

        run = self.a_run(text="the recognised words")
        mine = Bucket.objects.create(name="mine", owner=self.user)
        response = self.client.post(reverse("ocr:save", args=[run.pk]),
                                    {"bucket": mine.pk})
        self.assertEqual(response.status_code, 200, response.content)
        saved = VaultFile.objects.get(pk=response.json()["file_pk"])
        self.assertEqual(saved.file_type, "text")
        self.assertTrue(saved.title.endswith(".txt"))

    def test_nothing_is_saved_unless_asked(self):
        from toto.vault.models import VaultFile

        self.a_run()
        self.assertFalse(VaultFile.objects.filter(owner=self.user).exists())


class RetryTests(_Fixture):
    def test_retry_is_refused_once_the_source_has_been_swept(self):
        run = self.a_run(status=RunStatus.PARTIAL)
        run.pages.update(status="failed")
        run.discard_source()
        response = self.client.post(reverse("ocr:retry", args=[run.pk]))
        self.assertEqual(response.status_code, 409)
        self.assertIn("days", response.json()["error"])

    def test_retry_needs_something_to_retry(self):
        run = self.a_run()
        response = self.client.post(reverse("ocr:retry", args=[run.pk]))
        self.assertEqual(response.status_code, 400)


class GroupSubmitTests(_Fixture):
    def test_several_images_are_accepted_as_one_job(self):
        from toto.ocr import dispatch, engine

        files = [png_upload(f"p{n}.png") for n in range(3)]
        with mock.patch.object(engine, "tesseract_available", return_value=True), \
             mock.patch.object(engine, "is_offered", return_value=True), \
             mock.patch.object(dispatch, "dispatch_run"):
            response = self.client.post(reverse("ocr:submit"),
                                        {"document": files, "language": "eng"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["total_pages"], 3)
        run = OcrRun.objects.get(pk=response.json()["run_id"])
        self.assertEqual(len(run.sources), 3)
        self.assertIn("2 more", run.source_name)
