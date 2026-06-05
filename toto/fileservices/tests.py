"""
fileservices test suite.

The single media service is now "manta": picking it (or the wand "Open tool")
redirects to the manta command builder. ffmpeg/ffprobe/transcription/ocr are
backend-only (hidden from the menu).
"""

import os

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_fs")
class MantaServiceRedirectTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("fs_owner", password="pass")
        self.stranger = User.objects.create_user("fs_stranger", password="pass")
        self.client = DjangoClient()

        from toto.vault.models import Bucket
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

    def test_manta_service_redirects_to_builder(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(self.video), {"service_key": "manta"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn("/manta/", data["redirect_url"])
        self.assertIn(f"file={self.video.pk}", data["redirect_url"])

    def test_menu_shows_only_manta(self):
        self.client.login(username="fs_owner", password="pass")
        for vf in (self.video, self.audio, self.image):
            resp = self.client.get(reverse("fileservices:services_for_file", args=[vf.pk]))
            keys = {s["key"] for s in resp.json()["services"]}
            self.assertEqual(keys, {"manta"})  # ffmpeg/ffprobe/transcription/ocr are hidden

    def test_open_primary_routes_all_media_to_manta(self):
        self.client.login(username="fs_owner", password="pass")
        for vf in (self.video, self.audio, self.image):
            resp = self.client.get(reverse("fileservices:open_primary", args=[vf.pk]))
            self.assertEqual(resp.status_code, 302)
            self.assertIn("/manta/", resp.url)
            self.assertIn(f"file={vf.pk}", resp.url)

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
        self.client.post(self._run_url(self.video), {"service_key": "manta"})
        self.assertEqual(FileServiceRun.objects.count(), before)

    def test_inaccessible_file_is_denied(self):
        from toto.vault.models import Bucket, VaultFile
        other_bucket = Bucket.objects.create(name="Other", owner=self.owner, slug="fs-other")
        secret = VaultFile(owner=self.owner, title="secret.mp4", file_type="video",
                           bucket=other_bucket, is_public=False)
        secret.file.save("secret.mp4", ContentFile(b"x"), save=True)
        self.client.login(username="fs_stranger", password="pass")
        resp = self.client.post(self._run_url(secret), {"service_key": "manta"})
        self.assertEqual(resp.status_code, 403)
