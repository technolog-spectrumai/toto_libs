import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from toto.api.models import Connector as ApiConnector
from toto.core.connectors import list_connector_types, validate_connector_type
from toto.mandragora.management.commands.ingress_mandragora import Command
from toto.workflows.models import Report, ReportTemplate, WorkflowConnector


class MandragoraIngressTests(TestCase):
    def test_seed_connectors_creates_all_registered_connector_types(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(WORKFLOW_FILE_CONNECTOR_ROOT=tmpdir):
                Command()._seed_connectors()

            registered_types = {item["connector_type"] for item in list_connector_types()}
            seeded_types = set(WorkflowConnector.objects.values_list("connector_type", flat=True))

            self.assertTrue(registered_types.issubset(seeded_types))
            self.assertTrue((Path(tmpdir) / "ingress" / "sample-input.json").exists())
            self.assertTrue(ApiConnector.objects.filter(slug="httpbin-demo").exists())

            for connector in WorkflowConnector.objects.all():
                self.assertEqual(
                    validate_connector_type(connector.connector_type, connector.config),
                    [],
                )

    def test_seed_reports_creates_table_and_chart_reports_for_seeded_runs(self):
        command = Command()
        command._seed_workflows()
        command._seed_runs()
        command._seed_reports()

        self.assertTrue(ReportTemplate.objects.filter(slug="pipeline-output-table").exists())
        self.assertTrue(ReportTemplate.objects.filter(slug="enrichment-sections-chart").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-pipeline-output-table").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-enrichment-chart").exists())
        self.assertEqual(set(Report.objects.values_list("report_type", flat=True)), {"chart", "table"})
        self.assertEqual(Report.objects.filter(source_node_run__isnull=False).count(), 2)
