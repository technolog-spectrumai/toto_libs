"""OCR builder-page tests."""

import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_ocr")
class OcrRunPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("ocr_user", password="pass")
        self.client = DjangoClient()
        self.client.login(username="ocr_user", password="pass")
        p = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        from toto.vault.models import Bucket, VaultFile
        self.bucket = Bucket.objects.create(name="OC", owner=self.user, slug="oc")
        os.makedirs("/tmp/media_test_ocr/vault/files", exist_ok=True)
        self.image = VaultFile(owner=self.user, title="i.png", file_type="image", bucket=self.bucket)
        self.image.file.save("i.png", ContentFile(b"x"), save=True)
        self.audio = VaultFile(owner=self.user, title="a.mp3", file_type="audio", bucket=self.bucket)
        self.audio.file.save("a.mp3", ContentFile(b"x"), save=True)

    def test_page_renders_for_image(self):
        resp = self.client.get(reverse("ocr:run_page", args=[self.image.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "i.png")

    def test_page_rejects_non_image(self):
        resp = self.client.get(reverse("ocr:run_page", args=[self.audio.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_post_creates_run_and_redirects(self):
        from toto.fileservices.models import FileServiceRun
        before = FileServiceRun.objects.count()
        with patch("toto.ocr.views.dispatch_run") as disp:
            resp = self.client.post(reverse("ocr:run_page", args=[self.image.pk]),
                                    {"language": "eng"})
        self.assertEqual(FileServiceRun.objects.count(), before + 1)
        run = FileServiceRun.objects.latest("started_at")
        self.assertEqual(run.service_key, "ocr")
        self.assertEqual(run.args, "eng")
        self.assertTrue(disp.called)
        self.assertRedirects(resp, reverse("fileservices:run_detail", args=[run.id]),
                             fetch_redirect_response=False)
