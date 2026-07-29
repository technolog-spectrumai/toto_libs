"""Tests for the repurposed OCR tab (upload screenshot → Tesseract → text)."""

from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse


def _fake_helper():
    """An OcrHelper stand-in that yields two text lines without Tesseract."""
    helper = mock.Mock()
    helper.run_tesseract.return_value = {"raw": True}
    helper.extract_lines.return_value = [{"text": "hello"}, {"text": "world"}]
    return helper


@override_settings(MEDIA_ROOT="/tmp/media_test_ocr")
class OcrTabTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform

        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.admin = User.objects.create_superuser("ocr_admin", password="pass")
        self.plain = User.objects.create_user("ocr_plain", password="pass")
        self.client = Client()
        from toto.vault.models import Bucket

        self.bucket = Bucket.objects.create(name="OCR", owner=self.admin, slug="ocr-b")

    def _png(self):
        return SimpleUploadedFile("shot.png", b"\x89PNG\r\n\x1a\n", content_type="image/png")

    def test_home_renders_for_superuser(self):
        self.client.login(username="ocr_admin", password="pass")
        resp = self.client.get(reverse("ocr:home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Run OCR")

    def test_home_denied_for_non_superuser(self):
        self.client.login(username="ocr_plain", password="pass")
        resp = self.client.get(reverse("ocr:home"))
        self.assertEqual(resp.status_code, 302)  # user_passes_test → login redirect

    def test_run_extracts_text(self):
        self.client.login(username="ocr_admin", password="pass")
        with mock.patch("toto.ocr.ocr.OcrHelper", return_value=_fake_helper()):
            resp = self.client.post(reverse("ocr:run"), {"screenshot": self._png(), "language": "eng"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["text"], "hello\nworld")
        self.assertFalse(data["saved"])

    def test_run_requires_a_file(self):
        self.client.login(username="ocr_admin", password="pass")
        resp = self.client.post(reverse("ocr:run"), {"language": "eng"})
        self.assertEqual(resp.status_code, 400)

    def test_run_saves_to_bucket_when_requested(self):
        from toto.vault.models import VaultFile

        self.client.login(username="ocr_admin", password="pass")
        with mock.patch("toto.ocr.ocr.OcrHelper", return_value=_fake_helper()):
            resp = self.client.post(
                reverse("ocr:run"),
                {"screenshot": self._png(), "save_to_vault": "on", "bucket": self.bucket.pk},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["saved"])
        vf = VaultFile.objects.get(bucket=self.bucket)
        self.assertEqual(vf.file_type, "image")
        self.assertIsNone(vf.directory_id)

    def test_run_saves_into_chosen_directory(self):
        from toto.vault.models import VaultDirectory, VaultFile

        folder = VaultDirectory.objects.create(name="Screens", bucket=self.bucket, owner=self.admin)
        self.client.login(username="ocr_admin", password="pass")
        with mock.patch("toto.ocr.ocr.OcrHelper", return_value=_fake_helper()):
            resp = self.client.post(
                reverse("ocr:run"),
                {"screenshot": self._png(), "save_to_vault": "on",
                 "bucket": self.bucket.pk, "directory": folder.pk},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["saved"])
        vf = VaultFile.objects.get(bucket=self.bucket)
        self.assertEqual(vf.directory_id, folder.pk)

    def test_run_rejects_directory_from_other_bucket(self):
        from toto.vault.models import Bucket, VaultDirectory, VaultFile

        other = Bucket.objects.create(name="Other", owner=self.admin, slug="ocr-other")
        foreign = VaultDirectory.objects.create(name="Elsewhere", bucket=other, owner=self.admin)
        self.client.login(username="ocr_admin", password="pass")
        with mock.patch("toto.ocr.ocr.OcrHelper", return_value=_fake_helper()):
            resp = self.client.post(
                reverse("ocr:run"),
                {"screenshot": self._png(), "save_to_vault": "on",
                 "bucket": self.bucket.pk, "directory": foreign.pk},
            )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(VaultFile.objects.exists())

    def test_home_lists_directories(self):
        from toto.vault.models import VaultDirectory

        VaultDirectory.objects.create(name="Screens", bucket=self.bucket, owner=self.admin)
        self.client.login(username="ocr_admin", password="pass")
        resp = self.client.get(reverse("ocr:home"))
        self.assertContains(resp, "Screens")        # in the directories JSON
        self.assertContains(resp, "ocr-directories")

    def test_ingest_stashes_text_and_redirects(self):
        self.client.login(username="ocr_admin", password="pass")
        resp = self.client.post(reverse("ocr:ingest"), {"text": "hello world"})
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.url.endswith(reverse("ingestor:home")))
        self.assertEqual(self.client.session["ingest_text"], "hello world")

    def test_ingest_prefill_renders_on_ingestor_home(self):
        self.client.login(username="ocr_admin", password="pass")
        self.client.post(reverse("ocr:ingest"), {"text": "forwarded text"})
        resp = self.client.get(reverse("ingestor:home"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "forwarded text")          # injected prefill
        self.assertNotIn("ingest_text", self.client.session)  # popped after use

    def test_ingest_prefill_escapes_script_breakout(self):
        """A </script> payload in the forwarded text must not break out of the
        json_script block (XSS guard)."""
        self.client.login(username="ocr_admin", password="pass")
        payload = "</script><img src=x onerror=alert(1)>"
        self.client.post(reverse("ocr:ingest"), {"text": payload})
        resp = self.client.get(reverse("ingestor:home"))
        self.assertEqual(resp.status_code, 200)
        # json_script unicode-escapes angle brackets, so no raw breakout appears.
        self.assertNotContains(resp, "<img src=x onerror")
        self.assertNotContains(resp, "</script><img")
