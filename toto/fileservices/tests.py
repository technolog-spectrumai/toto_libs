"""
fileservices test suite.

Covers the media-service redirect: selecting ffmpeg/ffprobe on a vault file
routes the user to the videomant command builder instead of running ffmpeg
directly from a free-text arg string.
"""

import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_fs")
class MediaServiceRedirectTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("fs_owner", password="pass")
        self.stranger = User.objects.create_user("fs_stranger", password="pass")
        self.client = DjangoClient()

        from toto.vault.models import Bucket, VaultFile
        self.bucket = Bucket.objects.create(name="FS Bucket", owner=self.owner, slug="fs-bucket")
        os.makedirs("/tmp/media_test_fs/vault/files", exist_ok=True)
        self.vf = VaultFile(owner=self.owner, title="clip.mp4", file_type="video", bucket=self.bucket)
        self.vf.file.save("clip.mp4", ContentFile(b"x"), save=True)

    def _run_url(self, vf=None):
        return reverse("fileservices:run_service", args=[(vf or self.vf).pk])

    def test_ffmpeg_redirects_to_builder(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(), {"service_key": "ffmpeg"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn(f"/videomant/vault/{self.vf.pk}/builder/", data["redirect_url"])
        self.assertIn("service=ffmpeg", data["redirect_url"])

    def test_ffprobe_redirects_to_builder(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(), {"service_key": "ffprobe"})
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn("service=ffprobe", data["redirect_url"])

    def test_redirect_does_not_create_a_run(self):
        from .models import FileServiceRun
        self.client.login(username="fs_owner", password="pass")
        before = FileServiceRun.objects.count()
        self.client.post(self._run_url(), {"service_key": "ffmpeg"})
        self.assertEqual(FileServiceRun.objects.count(), before)  # builder defers the job

    def test_inaccessible_file_is_denied(self):
        from toto.vault.models import VaultFile
        private = VaultFile(owner=self.owner, title="private.mp4", file_type="video",
                            bucket=self.bucket, is_public=False)
        private.file.save("private.mp4", ContentFile(b"x"), save=True)
        self.client.login(username="fs_stranger", password="pass")
        resp = self.client.post(self._run_url(private), {"service_key": "ffmpeg"})
        self.assertEqual(resp.status_code, 403)
