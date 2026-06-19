"""
Tests for the standalone, workflow-driven OCR tool.
"""

import os
from unittest import mock

from django.apps import apps
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import TestCase, Client, override_settings
from django.urls import reverse


@override_settings(MEDIA_ROOT="/tmp/media_test_ocr")
class OcrViewTests(TestCase):
    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="Test", publication_year=2024, active=True)
        self.user = User.objects.create_user("ocr_user", password="pass")
        self.other = User.objects.create_user("ocr_other", password="pass")
        self.client = Client()
        from toto.vault.models import Bucket, VaultFile
        self.bucket = Bucket.objects.create(name="OCR", owner=self.user, slug="ocr-b")
        os.makedirs("/tmp/media_test_ocr/vault/files", exist_ok=True)
        self.image = VaultFile(owner=self.user, title="scan.png", file_type="image", bucket=self.bucket)
        self.image.file.save("scan.png", ContentFile(b"x"), save=True)

    def _run(self, **kw):
        from toto.fileservices.models import FileServiceRun
        defaults = dict(service_key="ocr", owner=self.user, input_file=self.image,
                        bucket=self.bucket, status=FileServiceRun.SUCCESS)
        defaults.update(kw)
        return FileServiceRun.objects.create(**defaults)

    def test_home_renders(self):
        self.client.login(username="ocr_user", password="pass")
        resp = self.client.get(reverse("ocr:home"))
        self.assertEqual(resp.status_code, 200)

    def test_run_creates_ocr_fileservice_run_and_redirects(self):
        from toto.fileservices.models import FileServiceRun
        self.client.login(username="ocr_user", password="pass")
        with mock.patch("toto.ocr.views.dispatch_run", return_value=True) as m:
            resp = self.client.post(reverse("ocr:home"), {"file": self.image.pk, "language": "eng+pol"})
        run = FileServiceRun.objects.get(owner=self.user, service_key="ocr")
        self.assertEqual(run.input_file_id, self.image.pk)
        self.assertEqual(run.args, "eng+pol")
        self.assertEqual(resp.status_code, 302)
        self.assertIn(reverse("ocr:result", args=[run.id]), resp.url)
        m.assert_called_once()

    def test_run_rejects_non_image(self):
        from toto.fileservices.models import FileServiceRun
        from toto.vault.models import VaultFile
        audio = VaultFile(owner=self.user, title="a.mp3", file_type="audio", bucket=self.bucket)
        audio.file.save("a.mp3", ContentFile(b"x"), save=True)
        self.client.login(username="ocr_user", password="pass")
        with mock.patch("toto.ocr.views.dispatch_run") as m:
            resp = self.client.post(reverse("ocr:home"), {"file": audio.pk})
        self.assertEqual(resp.status_code, 200)  # re-rendered home with error
        m.assert_not_called()
        self.assertFalse(FileServiceRun.objects.filter(owner=self.user).exists())

    def test_result_shows_extracted_text(self):
        from toto.vault.models import VaultFile
        out = VaultFile(owner=self.user, title="scan.ocr.txt", file_type="text", bucket=self.bucket)
        out.file.save("scan.ocr.txt", ContentFile(b"hello world"), save=True)
        run = self._run(output_file_pks=[out.pk])
        self.client.login(username="ocr_user", password="pass")
        resp = self.client.get(reverse("ocr:result", args=[run.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "hello world")

    def test_result_links_to_workflow_run_when_present(self):
        if not apps.is_installed("toto.workflows"):
            self.skipTest("workflows not installed")
        from toto.workflows.models import Workflow, WorkflowRun
        wf = Workflow.objects.create(slug="fileservices-run", name="File Service Run")
        wf_run = WorkflowRun.objects.create(workflow=wf, input_data={})
        run = self._run(workflow_run=wf_run)
        self.client.login(username="ocr_user", password="pass")
        resp = self.client.get(reverse("ocr:result", args=[run.id]))
        self.assertContains(resp, reverse("workflows:workflow_run_detail", args=[wf_run.id]))

    def test_status_endpoint(self):
        from toto.fileservices.models import FileServiceRun
        run = self._run(status=FileServiceRun.RUNNING)
        self.client.login(username="ocr_user", password="pass")
        data = self.client.get(reverse("ocr:status", args=[run.id])).json()
        self.assertEqual(data["status"], "running")
        self.assertFalse(data["is_terminal"])

    def test_result_denied_for_other_user(self):
        run = self._run()
        self.client.login(username="ocr_other", password="pass")
        self.assertEqual(self.client.get(reverse("ocr:result", args=[run.id])).status_code, 404)

    def test_bento_export_gated_on_graph_stack(self):
        run = self._run()
        self.client.login(username="ocr_user", password="pass")
        resp = self.client.get(reverse("ocr:export_bento", args=[run.id]))
        if apps.is_installed("toto.bento") and apps.is_installed("toto.ravioli"):
            self.assertEqual(resp.status_code, 200)
        else:
            self.assertEqual(resp.status_code, 404)
