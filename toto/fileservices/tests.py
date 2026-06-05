"""
fileservices test suite.

Covers the builder-service redirect: the consolidated "videomant" media service,
plus transcription (audio) and ocr (image), route the user to their app pages
instead of running inline from a free-text arg string.
"""

import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_fs")
class BuilderServiceRedirectTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("fs_owner", password="pass")
        self.stranger = User.objects.create_user("fs_stranger", password="pass")
        self.client = DjangoClient()

        from toto.vault.models import Bucket, VaultFile
        self.bucket = Bucket.objects.create(name="FS Bucket", owner=self.owner, slug="fs-bucket")
        os.makedirs("/tmp/media_test_fs/vault/files", exist_ok=True)
        self.video = self._vf("clip.mp4", "video")
        self.audio = self._vf("song.mp3", "audio")
        self.image = self._vf("scan.png", "image")

    def _vf(self, name, file_type, *, owner=None, is_public=False):
        from toto.vault.models import VaultFile
        vf = VaultFile(owner=owner or self.owner, title=name, file_type=file_type,
                       bucket=self.bucket, is_public=is_public)
        vf.file.save(name, ContentFile(b"x"), save=True)
        return vf

    def _run_url(self, vf):
        return reverse("fileservices:run_service", args=[vf.pk])

    def test_videomant_service_redirects_to_builder(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(self.video), {"service_key": "videomant"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn("/videomant/builder/", data["redirect_url"])
        self.assertIn(f"file={self.video.pk}", data["redirect_url"])

    def test_transcription_audio_redirects_to_app(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(self.audio), {"service_key": "transcription"})
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn(f"/transcription/vault/{self.audio.pk}/", data["redirect_url"])

    def test_ocr_image_redirects_to_app(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(self.image), {"service_key": "ocr"})
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn(f"/ocr/vault/{self.image.pk}/", data["redirect_url"])

    def test_ffmpeg_and_ffprobe_hidden_from_menu(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.get(reverse("fileservices:services_for_file", args=[self.video.pk]))
        keys = {s["key"] for s in resp.json()["services"]}
        self.assertEqual(keys, {"videomant"})  # ffmpeg/ffprobe collapsed into one entry

    def test_transcription_not_offered_for_video(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.get(reverse("fileservices:services_for_file", args=[self.video.pk]))
        keys = {s["key"] for s in resp.json()["services"]}
        self.assertNotIn("transcription", keys)

    def test_open_primary_routes_by_file_type(self):
        self.client.login(username="fs_owner", password="pass")
        cases = [
            (self.video, "/videomant/builder/", f"file={self.video.pk}"),
            (self.audio, f"/transcription/vault/{self.audio.pk}/", None),
            (self.image, f"/ocr/vault/{self.image.pk}/", None),
        ]
        for vf, expect, extra in cases:
            resp = self.client.get(reverse("fileservices:open_primary", args=[vf.pk]))
            self.assertEqual(resp.status_code, 302)
            self.assertIn(expect, resp.url)
            if extra:
                self.assertIn(extra, resp.url)

    def test_open_primary_denies_inaccessible(self):
        from toto.vault.models import Bucket, VaultFile
        other = Bucket.objects.create(name="O2", owner=self.owner, slug="fs-o2")
        secret = VaultFile(owner=self.owner, title="s.mp4", file_type="video", bucket=other, is_public=False)
        secret.file.save("s.mp4", ContentFile(b"x"), save=True)
        self.client.login(username="fs_stranger", password="pass")
        resp = self.client.get(reverse("fileservices:open_primary", args=[secret.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_redirect_does_not_create_a_run(self):
        from .models import FileServiceRun
        self.client.login(username="fs_owner", password="pass")
        before = FileServiceRun.objects.count()
        self.client.post(self._run_url(self.video), {"service_key": "videomant"})
        self.assertEqual(FileServiceRun.objects.count(), before)  # builder defers the job

    def test_inaccessible_file_is_denied(self):
        private = self._vf("private.mp4", "video", is_public=False)
        # Re-own to owner already; make a stranger who cannot access a non-public file
        # in a bucket they do not own.
        from toto.vault.models import Bucket, VaultFile
        other_bucket = Bucket.objects.create(name="Other", owner=self.owner, slug="fs-other")
        secret = VaultFile(owner=self.owner, title="secret.mp4", file_type="video",
                           bucket=other_bucket, is_public=False)
        secret.file.save("secret.mp4", ContentFile(b"x"), save=True)
        self.client.login(username="fs_stranger", password="pass")
        resp = self.client.post(self._run_url(secret), {"service_key": "videomant"})
        self.assertEqual(resp.status_code, 403)
