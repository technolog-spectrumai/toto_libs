"""
fileservices test suite.

ffmpeg / ffprobe / transcription executors are backend-only (hidden from the
menu). The manta media builder is optional (BUILD_MANTA) and lives in toto.manta.
OCR is no longer a file service — it lives as a Knowledge-Graph tab (toto.ocr).
"""

from django.test import TestCase


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
