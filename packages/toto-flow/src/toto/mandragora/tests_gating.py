"""
Demo workflows must not pollute a minimal (FULL_INGRESS=0) bring-up.

Kept in a standalone module (rather than tests.py) so it imports only names
that exist regardless of build flags.
"""
import tempfile

from django.test import TestCase, override_settings

from toto.mandragora.management.commands.ingress_mandragora import Command
from toto.workflows.models import Report, Workflow, WorkflowRun


class MandragoraDemoGatingTests(TestCase):
    def test_demo_workflows_skipped_without_full_ingress(self):
        cmd = Command()
        cmd.full = False
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(WORKFLOW_FILE_CONNECTOR_ROOT=tmpdir):
                cmd.process()
        # Sample workflows (and their demo runs/reports) are not seeded.
        self.assertFalse(Workflow.objects.filter(name="Data Pipeline").exists())
        self.assertFalse(Workflow.objects.filter(name="Human Approval Gate").exists())
        self.assertEqual(WorkflowRun.objects.count(), 0)
        self.assertEqual(Report.objects.count(), 0)
