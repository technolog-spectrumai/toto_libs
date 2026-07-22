import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from toto.api.models import Connector as ApiConnector
from toto.core.connectors import list_connector_types, validate_connector_type
from toto.mandragora.management.commands.ingress_mandragora import Command
from toto.workflows.models import (
    Report,
    ReportTemplate,
    WorkflowConnector,
    Workflow,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from toto.workflows.services.executor import WorkflowExecutor


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
            self.assertTrue(ApiConnector.objects.filter(slug="open-meteo-forecast").exists())

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
        self.assertTrue(ReportTemplate.objects.filter(slug="metrics-trend-line").exists())
        self.assertTrue(ReportTemplate.objects.filter(slug="channel-mix-pie").exists())
        self.assertTrue(ReportTemplate.objects.filter(slug="weather-temperature-line").exists())
        self.assertTrue(ReportTemplate.objects.filter(slug="weather-precipitation-bar").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-pipeline-output-table").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-enrichment-chart").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-metrics-trend-line").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-channel-mix-pie").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-weather-temperature-line").exists())
        self.assertTrue(Report.objects.filter(slug="ingress-weather-precipitation-bar").exists())
        self.assertEqual(set(Report.objects.values_list("report_type", flat=True)), {"chart", "table"})
        self.assertEqual(Report.objects.filter(source_node_run__isnull=False).count(), 6)
        self.assertEqual(
            set(ReportTemplate.objects.filter(slug__in=[
                "metrics-trend-line",
                "channel-mix-pie",
                "weather-temperature-line",
                "weather-precipitation-bar",
            ]).values_list("definition__chart", flat=True)),
            {"line", "pie", "bar"},
        )
        self.assertEqual(
            set(Report.objects.values_list("source_node_run__node__node_type", flat=True)),
            {WorkflowNode.REPORT},
        )
        self.assertEqual(
            set(Report.objects.values_list("source_node_run__node__label", flat=True)),
            {
                "Output Report",
                "Summary Chart",
                "Trend Line Report",
                "Channel Mix Report",
                "Temperature Report",
                "Precipitation Report",
            },
        )

    def test_seed_reports_backfills_existing_runs_with_report_nodes(self):
        command = Command()
        command._seed_linear_workflow()
        command._seed_approval_workflow()
        command._seed_fanout_workflow()
        command._seed_runs()
        command._seed_reports()

        self.assertEqual(
            set(Report.objects.values_list("source_node_run__node__node_type", flat=True)),
            {WorkflowNode.LAMBDA},
        )

        command._seed_workflows()
        command._seed_runs()
        command._seed_reports()

        self.assertEqual(WorkflowNodeRun.objects.filter(node__node_type=WorkflowNode.REPORT).count(), 8)
        self.assertEqual(
            set(Report.objects.values_list("source_node_run__node__node_type", flat=True)),
            {WorkflowNode.REPORT},
        )
        self.assertEqual(
            set(Report.objects.values_list("source_node_run__node__label", flat=True)),
            {
                "Output Report",
                "Summary Chart",
                "Trend Line Report",
                "Channel Mix Report",
                "Temperature Report",
                "Precipitation Report",
            },
        )

    def test_weather_workflow_fetches_forecast_and_generates_two_reports(self):
        command = Command()
        command._seed_connectors()
        templates = command._seed_report_templates()
        command._seed_weather_workflow(
            templates["weather_temperature"],
            templates["weather_precipitation"],
        )

        wf = Workflow.objects.get(name="Weather Forecast Report")
        run = WorkflowRun.objects.create(workflow=wf)
        payload = command._weather_forecast_payload()

        class FakeWeatherResponse:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, *args):
                return json.dumps(payload).encode("utf-8")

            def getcode(self):
                return self.status

        with patch("toto.api.client.urlopen", return_value=FakeWeatherResponse()):
            WorkflowExecutor().start(run)

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(Report.objects.filter(workflow_run=run).count(), 2)
        self.assertEqual(
            set(Report.objects.filter(workflow_run=run).values_list("title", flat=True)),
            {"Poznan Temperature Forecast", "Poznan Precipitation Forecast"},
        )
        self.assertEqual(
            set(
                Report.objects.filter(workflow_run=run).values_list(
                    "source_node_run__node__label",
                    flat=True,
                )
            ),
            {"Temperature Report", "Precipitation Report"},
        )
