"""Transcription builder-page tests."""

import os
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_transcription")
class TranscriptionRunPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("tr_user", password="pass")
        self.client = DjangoClient()
        self.client.login(username="tr_user", password="pass")
        p = patch("toto.ui.page.PageProcessor._get_config", return_value=None)
        p.start()
        self.addCleanup(p.stop)
        from toto.vault.models import Bucket, VaultFile
        self.bucket = Bucket.objects.create(name="TR", owner=self.user, slug="tr")
        os.makedirs("/tmp/media_test_transcription/vault/files", exist_ok=True)
        self.audio = VaultFile(owner=self.user, title="a.mp3", file_type="audio", bucket=self.bucket)
        self.audio.file.save("a.mp3", ContentFile(b"x"), save=True)
        self.video = VaultFile(owner=self.user, title="v.mp4", file_type="video", bucket=self.bucket)
        self.video.file.save("v.mp4", ContentFile(b"x"), save=True)

    def test_page_renders_for_audio(self):
        resp = self.client.get(reverse("transcription:run_page", args=[self.audio.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "a.mp3")

    def test_page_rejects_video(self):
        resp = self.client.get(reverse("transcription:run_page", args=[self.video.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_post_creates_run_and_redirects(self):
        from toto.fileservices.models import FileServiceRun
        before = FileServiceRun.objects.count()
        with patch("toto.transcription.views.dispatch_run") as disp:
            resp = self.client.post(reverse("transcription:run_page", args=[self.audio.pk]),
                                    {"language": "en"})
        self.assertEqual(FileServiceRun.objects.count(), before + 1)
        run = FileServiceRun.objects.latest("started_at")
        self.assertEqual(run.service_key, "transcription")
        self.assertEqual(run.args, "en")
        self.assertTrue(disp.called)
        self.assertRedirects(resp, reverse("fileservices:run_detail", args=[run.id]),
                             fetch_redirect_response=False)
