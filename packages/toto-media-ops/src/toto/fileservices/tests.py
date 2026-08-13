"""
fileservices test suite.

ffmpeg / ffprobe executors are backend-only (hidden from the
menu). The manta media builder is optional (BUILD_MANTA) and lives in toto.manta.
OCR is no longer a file service — it lives as a Knowledge-Graph tab (toto.ocr).
"""

import tempfile

from django.test import TestCase, override_settings


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


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="fileservices-access-"))
class RunServiceAccessTests(TestCase):
    """The runner must refuse a file the caller cannot read.

    The check used to sit INSIDE ``if plugin.builder:``, so every non-builder
    service — ffmpeg, ffprobe, transcription — skipped it. A POST naming another
    user's file pk staged their bytes into a temp dir, ran a lossless remux over
    them, and filed the OUTPUT as a VaultFile owned by the CALLER. ffprobe was
    the metadata variant and transcription the transcript one.
    """

    @classmethod
    def setUpTestData(cls):
        from django.contrib.auth import get_user_model

        from toto.vault.models import Bucket

        User = get_user_model()
        cls.owner = User.objects.create_user("victim", password="pw")
        cls.attacker = User.objects.create_user("mallory", password="pw")
        cls.bucket = Bucket.objects.create(name="Theirs", slug="theirs",
                                           owner=cls.owner)

    def _victim_file(self):
        from django.core.files.base import ContentFile

        from toto.vault.models import VaultFile

        vault_file = VaultFile(owner=self.owner, title="private.mp4",
                               file_type="video", bucket=self.bucket)
        vault_file.file.save("private.mp4", ContentFile(b"\x00\x00"), save=False)
        vault_file.save()
        return vault_file

    def test_a_stranger_cannot_run_a_service_on_your_file(self):
        from django.urls import reverse

        from toto.fileservices.models import FileServiceRun

        vault_file = self._victim_file()
        self.client.force_login(self.attacker)

        response = self.client.post(
            reverse("fileservices:run_service", args=[vault_file.pk]),
            {"service_key": "ffprobe", "args": ""})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(FileServiceRun.objects.count(), 0)

    def test_a_stranger_cannot_even_list_the_services_for_your_file(self):
        """It returns the file's TITLE — unchecked, that reads names by pk."""
        from django.urls import reverse

        vault_file = self._victim_file()
        self.client.force_login(self.attacker)

        response = self.client.get(
            reverse("fileservices:services_for_file", args=[vault_file.pk]))

        self.assertEqual(response.status_code, 404)

    def test_the_owner_is_unaffected(self):
        from django.urls import reverse

        vault_file = self._victim_file()
        self.client.force_login(self.owner)

        response = self.client.get(
            reverse("fileservices:services_for_file", args=[vault_file.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["file_title"], "private.mp4")
