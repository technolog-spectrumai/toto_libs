"""
fileservices test suite.

OCR has been split out of manta into a standalone tool: for images the vault
wand shows the "ocr_tool" builder, which redirects to the OCR page. ffmpeg /
ffprobe / ocr / transcription executors are backend-only (hidden from the menu).
The manta media builder is now optional (BUILD_MANTA) and lives in toto.manta.
"""

import os

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client as DjangoClient, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_fs")
class OcrServiceRedirectTests(TestCase):
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

    def test_ocr_service_redirects_to_page(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.post(self._run_url(self.image), {"service_key": "ocr_tool"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "redirect")
        self.assertIn("/ocr/", data["redirect_url"])
        self.assertIn(f"file={self.image.pk}", data["redirect_url"])

    def test_menu_shows_ocr_for_image_only(self):
        self.client.login(username="fs_owner", password="pass")
        # Image → OCR builder is the single visible service.
        resp = self.client.get(reverse("fileservices:services_for_file", args=[self.image.pk]))
        keys = {s["key"] for s in resp.json()["services"]}
        self.assertIn("ocr_tool", keys)
        # Hidden executors never surface in the menu.
        self.assertNotIn("ocr", keys)
        self.assertNotIn("ffmpeg", keys)
        self.assertNotIn("transcription", keys)
        # Without manta installed, video/audio have no visible builder.
        if not apps.is_installed("toto.manta"):
            for vf in (self.video, self.audio):
                resp = self.client.get(reverse("fileservices:services_for_file", args=[vf.pk]))
                self.assertEqual({s["key"] for s in resp.json()["services"]}, set())

    def test_open_primary_routes_image_to_ocr(self):
        self.client.login(username="fs_owner", password="pass")
        resp = self.client.get(reverse("fileservices:open_primary", args=[self.image.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/ocr/", resp.url)
        self.assertIn(f"file={self.image.pk}", resp.url)

    def test_open_primary_denies_inaccessible(self):
        from toto.vault.models import Bucket, VaultFile
        other = Bucket.objects.create(name="O2", owner=self.owner, slug="fs-o2")
        secret = VaultFile(owner=self.owner, title="s.png", file_type="image", bucket=other, is_public=False)
        secret.file.save("s.png", ContentFile(b"x"), save=True)
        self.client.login(username="fs_stranger", password="pass")
        resp = self.client.get(reverse("fileservices:open_primary", args=[secret.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_redirect_does_not_create_a_run(self):
        from .models import FileServiceRun
        self.client.login(username="fs_owner", password="pass")
        before = FileServiceRun.objects.count()
        self.client.post(self._run_url(self.image), {"service_key": "ocr_tool"})
        self.assertEqual(FileServiceRun.objects.count(), before)

    def test_inaccessible_file_is_denied(self):
        from toto.vault.models import Bucket, VaultFile
        other_bucket = Bucket.objects.create(name="Other", owner=self.owner, slug="fs-other")
        secret = VaultFile(owner=self.owner, title="secret.png", file_type="image",
                           bucket=other_bucket, is_public=False)
        secret.file.save("secret.png", ContentFile(b"x"), save=True)
        self.client.login(username="fs_stranger", password="pass")
        resp = self.client.post(self._run_url(secret), {"service_key": "ocr_tool"})
        self.assertEqual(resp.status_code, 403)


class FileServicesIngressTests(TestCase):
    def test_seeds_generic_run_workflow(self):
        from django.core.management import call_command
        from toto.workflows.models import Workflow, WorkflowNode

        call_command("ingress_fileservices")
        wf = Workflow.objects.get(slug="fileservices-run")
        self.assertTrue(
            wf.nodes.filter(
                node_type=WorkflowNode.PREDEFINED_TASK, task_name="fileservice_run"
            ).exists()
        )

    def test_ingress_is_idempotent(self):
        from django.core.management import call_command
        from toto.workflows.models import Workflow

        call_command("ingress_fileservices")
        call_command("ingress_fileservices")
        self.assertEqual(Workflow.objects.filter(slug="fileservices-run").count(), 1)
