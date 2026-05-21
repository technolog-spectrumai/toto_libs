"""
Workflow DAG tests.

KernelClient is mocked via settings.WORKFLOW_KERNEL_CLIENT — the executor
resolves this setting at call time so no real kernel process is needed.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from celery.exceptions import SoftTimeLimitExceeded
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.test import RequestFactory, TestCase, override_settings
from django.utils import timezone

from .models import (
    HumanTask,
    LambdaFunction,
    Report,
    ReportTemplate,
    WorkflowConnector,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
    WorkflowTriggerInput,
)
from .services.executor import WorkflowExecutor
from .services.human_task import apply_output_mapping, submit_human_task
from .services.validator import ValidationError, WorkflowValidator
from .output import WorkflowOutput, normalize_workflow_output
from .tasks import execute_lambda_node_task
from toto.api.models import Connector as ApiConnectorModel
from toto.mandragora.connectors import execute_connector as execute_mandragora_connector
from toto.mandragora.connectors import list_available_connectors


def _make_kernel_mock(stdout_payload: dict):
    """Return a mock KernelClient class whose execute() returns a fixed payload."""
    mock_instance = MagicMock()
    mock_instance.execute.return_value = {"stdout": json.dumps(stdout_payload)}

    class MockKernelClient:
        def __new__(cls, *args, **kwargs):
            return mock_instance

    return MockKernelClient


def _lambda(name="fn") -> LambdaFunction:
    return LambdaFunction.objects.create(function_name=name, content="pass")


def _node(
    workflow,
    node_type,
    label="",
    lambda_fn=None,
    connector=None,
    report_template=None,
    config=None,
) -> WorkflowNode:
    return WorkflowNode.objects.create(
        workflow=workflow,
        node_type=node_type,
        label=label,
        lambda_function=lambda_fn,
        connector=connector,
        report_template=report_template,
        config=config or {},
    )


def _edge(workflow, source, target, branch_key="", is_default=False) -> WorkflowEdge:
    return WorkflowEdge.objects.create(
        workflow=workflow,
        source=source,
        target=target,
        branch_key=branch_key,
        is_default=is_default,
    )


# ---------------------------------------------------------------------------
#  normalize_workflow_output
# ---------------------------------------------------------------------------

class NormalizeOutputTests(TestCase):

    def test_route_string(self):
        wo = normalize_workflow_output({"data": {"x": 1}, "route": "yes"})
        self.assertEqual(wo.routes, ["yes"])
        self.assertEqual(wo.data, {"x": 1})

    def test_routes_list(self):
        wo = normalize_workflow_output({"data": {}, "routes": ["a", "b"]})
        self.assertEqual(wo.routes, ["a", "b"])

    def test_no_route(self):
        wo = normalize_workflow_output({"data": {"k": "v"}})
        self.assertEqual(wo.routes, [])

    def test_non_dict_input(self):
        wo = normalize_workflow_output("garbage")
        self.assertIsInstance(wo, WorkflowOutput)
        self.assertEqual(wo.routes, [])

    def test_empty_dict(self):
        wo = normalize_workflow_output({})
        self.assertEqual(wo.data, {})
        self.assertEqual(wo.routes, [])


# ---------------------------------------------------------------------------
#  WorkflowValidator
# ---------------------------------------------------------------------------

class ValidatorTests(TestCase):

    def _workflow(self, name="W"):
        return Workflow.objects.create(name=name)

    def test_empty_workflow_fails(self):
        wf = self._workflow()
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("no nodes" in e for e in ctx.exception.errors))

    def test_lambda_without_function_fails(self):
        wf = self._workflow()
        _node(wf, WorkflowNode.LAMBDA, label="bad")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("no lambda_function" in e for e in ctx.exception.errors))

    def test_human_without_schema_fails(self):
        wf = self._workflow()
        _node(wf, WorkflowNode.HUMAN, label="h", config={})
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("config['schema']" in e for e in ctx.exception.errors))

    def test_connector_without_connector_fails(self):
        wf = self._workflow()
        _node(wf, WorkflowNode.CONNECTOR, label="missing")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("has no connector" in e for e in ctx.exception.errors))

    def test_report_without_template_fails(self):
        wf = self._workflow()
        _node(wf, WorkflowNode.REPORT, label="missing-report")
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("has no report_template" in e for e in ctx.exception.errors))

    def test_split_without_single_incoming_fails(self):
        wf = self._workflow()
        fn = _lambda("f1")
        s = _node(wf, WorkflowNode.SPLIT, label="S")
        n1 = _node(wf, WorkflowNode.LAMBDA, label="N1", lambda_fn=fn)
        n2 = _node(wf, WorkflowNode.LAMBDA, label="N2", lambda_fn=_lambda("f2"))
        _edge(wf, n1, s)
        _edge(wf, n2, s)
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("exactly one incoming" in e for e in ctx.exception.errors))

    def test_cycle_detected(self):
        wf = self._workflow()
        fn = _lambda("loop")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn)
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=_lambda("loop2"))
        _edge(wf, a, b)
        _edge(wf, b, a)
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("cycle" in e for e in ctx.exception.errors))

    def test_valid_linear_passes(self):
        wf = self._workflow()
        fn1, fn2 = _lambda("a"), _lambda("b")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn1)
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=fn2)
        _edge(wf, a, b)
        WorkflowValidator().validate(wf)

    def test_self_loop_fails(self):
        wf = self._workflow()
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=_lambda("sl"))
        _edge(wf, a, a)
        with self.assertRaises(ValidationError) as ctx:
            WorkflowValidator().validate(wf)
        self.assertTrue(any("self-loop" in e for e in ctx.exception.errors))


# ---------------------------------------------------------------------------
#  Linear lambda workflow
# ---------------------------------------------------------------------------

class LinearLambdaWorkflowTests(TestCase):

    def _run_workflow(self, mock_output: dict) -> WorkflowRun:
        MockClient = _make_kernel_mock(mock_output)
        wf = Workflow.objects.create(name="Linear")
        fn1, fn2 = _lambda("step1"), _lambda("step2")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn1)
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=fn2)
        _edge(wf, a, b)

        run = WorkflowRun.objects.create(workflow=wf, input_data={"seed": 1})
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        return run

    def test_both_nodes_complete(self):
        run = self._run_workflow({"data": {"ok": True}, "route": "next"})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(run.node_runs.count(), 2)
        self.assertTrue(run.node_runs.filter(status=WorkflowNodeRun.COMPLETED).count() == 2)

    def test_output_propagates(self):
        run = self._run_workflow({"data": {"msg": "hello"}, "route": "next"})
        b_run = run.node_runs.filter(node__label="B").first()
        self.assertIsNotNone(b_run)
        self.assertEqual(b_run.input_data.get("data", {}).get("msg"), "hello")

    def test_kernel_error_fails_run(self):
        instance = MagicMock()
        instance.execute.return_value = {"error": "kernel_crash"}

        wf = Workflow.objects.create(name="Err")
        fn = _lambda("err_fn")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=fn)
        run = WorkflowRun.objects.create(workflow=wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=type("MC", (), {"__new__": lambda cls, *a, **k: instance})):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.FAILED)


# ---------------------------------------------------------------------------
#  Connector workflow nodes
# ---------------------------------------------------------------------------

class ConnectorWorkflowTests(TestCase):

    def test_connector_registry_exposes_app_connector_classes_to_mandragora(self):
        connectors = list_available_connectors()
        connector_types = {item["connector_type"] for item in connectors}
        connector_apps = {item["connector_type"]: item["app_label"] for item in connectors}
        self.assertIn(WorkflowConnector.API_REQUEST, connector_types)
        self.assertEqual(connector_apps[WorkflowConnector.API_REQUEST], "api")
        self.assertIn(WorkflowConnector.PEOPLE_READ, connector_types)
        self.assertIn(WorkflowConnector.SOCIALHUB_READ, connector_types)
        self.assertIn(WorkflowConnector.LOCATIONS_READ, connector_types)
        self.assertIn(WorkflowConnector.EVENTS_READ, connector_types)

    def test_file_write_connector_writes_input_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            connector = WorkflowConnector.objects.create(
                name="write-result",
                connector_type=WorkflowConnector.FILE_WRITE,
                config={
                    "path": "runs/result.txt",
                    "content_field": "data.message",
                },
            )
            wf = Workflow.objects.create(name="FileWrite")
            _node(wf, WorkflowNode.CONNECTOR, label="write", connector=connector)
            run = WorkflowRun.objects.create(
                workflow=wf,
                input_data={"data": {"message": "hello workflow"}},
            )

            with override_settings(WORKFLOW_FILE_CONNECTOR_ROOT=tmpdir):
                WorkflowExecutor().start(run)

            run.refresh_from_db()
            self.assertEqual(run.status, WorkflowRun.COMPLETED)
            self.assertEqual((Path(tmpdir) / "runs/result.txt").read_text(), "hello workflow")
            output = run.node_runs.get().output_data
            self.assertEqual(output["data"]["path"], "runs/result.txt")

    def test_file_read_connector_returns_text_and_json(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "input.json"
            path.write_text('{"answer": 42}')
            connector = WorkflowConnector.objects.create(
                name="read-json",
                connector_type=WorkflowConnector.FILE_READ,
                config={"path": "input.json"},
            )
            wf = Workflow.objects.create(name="FileRead")
            _node(wf, WorkflowNode.CONNECTOR, label="read", connector=connector)
            run = WorkflowRun.objects.create(workflow=wf)

            with override_settings(WORKFLOW_FILE_CONNECTOR_ROOT=tmpdir):
                WorkflowExecutor().start(run)

            output = run.node_runs.get().output_data
            self.assertEqual(output["data"]["content"], '{"answer": 42}')
            self.assertEqual(output["data"]["json"], {"answer": 42})

    def test_api_connector_returns_response_data(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, *args):
                return b'{"ok": true}'

            def getcode(self):
                return self.status

        connector = WorkflowConnector.objects.create(
            name="call-api",
            connector_type=WorkflowConnector.API_REQUEST,
            config={
                "url": "https://api.example.test/items",
                "method": "POST",
                "json_field": "data.payload",
            },
        )
        wf = Workflow.objects.create(name="ApiCall")
        _node(wf, WorkflowNode.CONNECTOR, label="api", connector=connector)
        run = WorkflowRun.objects.create(
            workflow=wf,
            input_data={"data": {"payload": {"name": "Ada"}}},
        )

        with patch("toto.api.client.urlopen", return_value=FakeResponse()) as mock_urlopen:
            WorkflowExecutor().start(run)

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b'{"name": "Ada"}')
        output = run.node_runs.get().output_data
        self.assertEqual(output["data"]["status_code"], 200)
        self.assertEqual(output["data"]["json"], {"ok": True})

    def test_api_connector_can_use_saved_core_api_connector(self):
        class FakeResponse:
            status = 200
            headers = {"Content-Type": "application/json"}

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, *args):
                return b'{"created": true}'

            def getcode(self):
                return self.status

        api_connector = ApiConnectorModel.objects.create(
            name="Example API",
            slug="example-api",
            base_url="https://api.example.test/v1/",
            auth_type=ApiConnectorModel.AUTH_NONE,
        )
        connector = WorkflowConnector.objects.create(
            name="call-saved-api",
            connector_type=WorkflowConnector.API_REQUEST,
            config={
                "api_connector_slug": api_connector.slug,
                "endpoint": "items",
                "method": "POST",
                "json_field": "data.payload",
            },
        )
        wf = Workflow.objects.create(name="SavedApiCall")
        _node(wf, WorkflowNode.CONNECTOR, label="api", connector=connector)
        run = WorkflowRun.objects.create(
            workflow=wf,
            input_data={"data": {"payload": {"name": "Ada"}}},
        )

        with patch("toto.api.client.urlopen", return_value=FakeResponse()) as mock_urlopen:
            WorkflowExecutor().start(run)

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.example.test/v1/items")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.data, b'{"name": "Ada"}')
        output = run.node_runs.get().output_data
        self.assertEqual(output["data"]["json"], {"created": True})

    def test_saved_api_connector_auth_requires_vault_session(self):
        api_connector = ApiConnectorModel.objects.create(
            name="Secret API",
            slug="secret-api",
            base_url="https://api.example.test/v1/",
            auth_type=ApiConnectorModel.AUTH_BEARER_TOKEN,
        )
        connector = WorkflowConnector.objects.create(
            name="call-secret-api",
            connector_type=WorkflowConnector.API_REQUEST,
            config={"api_connector_slug": api_connector.slug, "endpoint": "items"},
        )
        wf = Workflow.objects.create(name="SecretApiCall")
        _node(wf, WorkflowNode.CONNECTOR, label="api", connector=connector)
        run = WorkflowRun.objects.create(workflow=wf)

        WorkflowExecutor().start(run)

        run.refresh_from_db()
        node_run = run.node_runs.get()
        self.assertEqual(run.status, WorkflowRun.FAILED)
        self.assertIn("vault session", node_run.error)

    def test_people_read_connector_searches_people_without_writes(self):
        from toto.people.models import Person

        Person.objects.create(display_name="Ada Lovelace", slug="ada-lovelace", bio="Computing")
        Person.objects.create(display_name="Grace Hopper", slug="grace-hopper", bio="Compiler")
        connector = WorkflowConnector.objects.create(
            name="search-people",
            connector_type=WorkflowConnector.PEOPLE_READ,
            config={"action": "search", "query_field": "data.query"},
        )
        wf = Workflow.objects.create(name="PeopleRead")
        _node(wf, WorkflowNode.CONNECTOR, label="people", connector=connector)
        run = WorkflowRun.objects.create(workflow=wf, input_data={"data": {"query": "Ada"}})

        WorkflowExecutor().start(run)

        run.refresh_from_db()
        output = run.node_runs.get().output_data
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual([p["display_name"] for p in output["data"]["people"]], ["Ada Lovelace"])
        self.assertEqual(Person.objects.count(), 2)

    def test_mandragora_can_execute_people_connector(self):
        from toto.people.models import Person

        Person.objects.create(display_name="Ada Lovelace", slug="ada-lovelace", bio="Computing")

        output = execute_mandragora_connector(
            WorkflowConnector.PEOPLE_READ,
            {"action": "search", "query": "Ada"},
            {},
        )

        self.assertEqual(output["data"]["people"][0]["display_name"], "Ada Lovelace")

    def test_socialhub_read_connector_gets_community(self):
        from toto.socialhub.models import Community

        Community.objects.create(name="Open Guild", slug="open-guild", org_type=Community.GUILD)
        connector = WorkflowConnector.objects.create(
            name="get-community",
            connector_type=WorkflowConnector.SOCIALHUB_READ,
            config={"resource": "community", "action": "get", "lookup": "slug", "value": "open-guild"},
        )
        wf = Workflow.objects.create(name="SocialhubRead")
        _node(wf, WorkflowNode.CONNECTOR, label="community", connector=connector)
        run = WorkflowRun.objects.create(workflow=wf)

        WorkflowExecutor().start(run)

        output = run.node_runs.get().output_data
        self.assertEqual(output["data"]["community"]["name"], "Open Guild")
        self.assertEqual(output["data"]["community"]["org_type"], Community.GUILD)

    def test_locations_read_connector_lists_addresses(self):
        from toto.locations.models import Address

        Address.objects.create(country_name="PL", locality_name="Krakow", street="Grodzka", building="1")
        Address.objects.create(country_name="PL", locality_name="Warsaw", street="Krolewska", building="2")
        connector = WorkflowConnector.objects.create(
            name="search-addresses",
            connector_type=WorkflowConnector.LOCATIONS_READ,
            config={"resource": "address", "action": "search", "query": "Krakow"},
        )
        wf = Workflow.objects.create(name="LocationsRead")
        _node(wf, WorkflowNode.CONNECTOR, label="addresses", connector=connector)
        run = WorkflowRun.objects.create(workflow=wf)

        WorkflowExecutor().start(run)

        output = run.node_runs.get().output_data
        self.assertEqual(len(output["data"]["addresses"]), 1)
        self.assertEqual(output["data"]["addresses"][0]["locality_name"], "Krakow")

    def test_events_read_connector_searches_scheduled_events(self):
        from datetime import timedelta

        from toto.events.models import EventCategory, ScheduledEvent

        category = EventCategory.objects.create(name="Workshop", description="Hands-on")
        ScheduledEvent.objects.create(
            title="Workflow Automation Workshop",
            description="Build useful automations",
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=2),
            category=category,
            public=True,
        )
        ScheduledEvent.objects.create(
            title="Private Planning",
            description="Hidden",
            start_time=timezone.now() + timedelta(days=2),
            end_time=timezone.now() + timedelta(days=2, hours=1),
            category=category,
            public=False,
        )
        connector = WorkflowConnector.objects.create(
            name="search-events",
            connector_type=WorkflowConnector.EVENTS_READ,
            config={"resource": "scheduled_event", "action": "search", "query": "Automation"},
        )
        wf = Workflow.objects.create(name="EventsRead")
        _node(wf, WorkflowNode.CONNECTOR, label="events", connector=connector)
        run = WorkflowRun.objects.create(workflow=wf)

        WorkflowExecutor().start(run)

        output = run.node_runs.get().output_data
        self.assertEqual(len(output["data"]["events"]), 1)
        self.assertEqual(output["data"]["events"][0]["title"], "Workflow Automation Workshop")
        self.assertEqual(output["data"]["events"][0]["category"]["name"], "Workshop")

    def test_events_read_connector_lists_availability_for_person(self):
        from datetime import timedelta

        from toto.events.models import Availability
        from toto.people.models import Person

        person = Person.objects.create(display_name="Ada Lovelace", slug="ada-lovelace")
        Availability.objects.create(
            person=person,
            start_time=timezone.now() + timedelta(days=1),
            end_time=timezone.now() + timedelta(days=1, hours=1),
            availability_type=Availability.AvailabilityType.BUSY,
            reason="Workshop",
        )
        connector = WorkflowConnector.objects.create(
            name="person-availability",
            connector_type=WorkflowConnector.EVENTS_READ,
            config={"resource": "availability", "person_slug": "ada-lovelace"},
        )
        wf = Workflow.objects.create(name="AvailabilityRead")
        _node(wf, WorkflowNode.CONNECTOR, label="availability", connector=connector)
        run = WorkflowRun.objects.create(workflow=wf)

        WorkflowExecutor().start(run)

        output = run.node_runs.get().output_data
        self.assertEqual(len(output["data"]["availabilities"]), 1)
        self.assertEqual(output["data"]["availabilities"][0]["person"]["display_name"], "Ada Lovelace")
        self.assertEqual(output["data"]["availabilities"][0]["availability_type"], Availability.AvailabilityType.BUSY)


# ---------------------------------------------------------------------------
#  Report workflow nodes
# ---------------------------------------------------------------------------

class ReportWorkflowTests(TestCase):

    def _template(self):
        return ReportTemplate.objects.create(
            name="Metrics report",
            definition={
                "version": 1,
                "type": "table",
                "title": "Items",
                "data": {"path": "items"},
                "columns": [
                    {"key": "name", "label": "Name"},
                    {"key": "value", "label": "Value"},
                ],
            },
        )

    def _chart_template(self):
        return ReportTemplate.objects.create(
            name="Metrics chart",
            definition={
                "version": 1,
                "type": "chart",
                "title": "Series",
                "chart": "bar",
                "data": {"path": "series"},
                "x": "label",
                "y": "value",
            },
        )

    def test_report_node_materializes_pages_from_workflow_output(self):
        MockClient = _make_kernel_mock({
            "data": {
                "title": "Generated metrics",
                "metrics": {"total": 42},
                "items": [{"name": "Ada", "value": 30}, {"name": "Grace", "value": 12}],
                "series": [{"label": "A", "value": 30}, {"label": "B", "value": 12}],
            },
            "route": "report",
        })
        template = self._template()
        wf = Workflow.objects.create(name="ReportFlow")
        start = _node(wf, WorkflowNode.LAMBDA, label="Metrics", lambda_fn=_lambda("metrics"))
        report_node = _node(
            wf,
            WorkflowNode.REPORT,
            label="Report",
            report_template=template,
            config={"title_field": "data.title"},
        )
        _edge(wf, start, report_node)
        run = WorkflowRun.objects.create(workflow=wf)

        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)

        run.refresh_from_db()
        report = Report.objects.get()
        page = report.pages.get()
        report_run = run.node_runs.get(node=report_node)
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(report.title, "Generated metrics")
        self.assertEqual(report.report_type, "table")
        self.assertEqual(page.key, "table")
        self.assertEqual(page.data["items"][0]["name"], "Ada")
        self.assertEqual(report_run.output_data["data"]["report"]["id"], report.id)

    def test_report_renderer_resolves_table_values(self):
        from .services.reports import create_report, render_report

        template = self._template()
        report = create_report(
            template=template,
            title="Rendered metrics",
            data={
                "metrics": {"total": 42},
                "items": [{"name": "Ada", "value": 30}],
                "series": [{"label": "A", "value": 30}, {"label": "B", "value": 15}],
            },
        )

        blocks = render_report(report)[0]["blocks"]
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], "table")
        self.assertEqual(blocks[0]["rows"][0]["values"], ["Ada", 30])

    def test_report_renderer_resolves_chart_values(self):
        from .services.reports import create_report, render_report

        template = self._chart_template()
        report = create_report(
            template=template,
            title="Rendered chart",
            data={"series": [{"label": "A", "value": 30}, {"label": "B", "value": 15}]},
        )

        block = render_report(report)[0]["blocks"][0]
        self.assertEqual(report.report_type, "chart")
        self.assertEqual(block["points"][0]["percent"], 100)
        self.assertEqual(block["points"][1]["percent"], 50)
        self.assertIn("x_percent", block["points"][0])
        self.assertIn("line_points", block)

    def test_report_renderer_adds_pie_metadata(self):
        from .services.reports import create_report, render_report

        template = ReportTemplate.objects.create(
            name="Pie chart",
            definition={
                "version": 1,
                "type": "chart",
                "title": "Mix",
                "chart": "pie",
                "data": {"path": "series"},
                "x": "label",
                "y": "value",
            },
        )
        report = create_report(
            template=template,
            title="Rendered pie",
            data={"series": [{"label": "A", "value": 25}, {"label": "B", "value": 75}]},
        )

        block = render_report(report)[0]["blocks"][0]
        self.assertEqual(block["chart"], "pie")
        self.assertEqual(block["points"][0]["share_percent"], 25.0)
        self.assertEqual(block["points"][1]["share_percent"], 75.0)
        self.assertIn("conic-gradient", block["pie_gradient"])

    def test_report_definition_rejects_multiple_blocks(self):
        from django.core.exceptions import ValidationError as DjangoValidationError

        template = ReportTemplate(
            name="Bad report",
            definition={
                "version": 1,
                "pages": [
                    {
                        "key": "bad",
                        "blocks": [
                            {"type": "table", "columns": [{"key": "name"}]},
                            {"type": "chart", "data": {"path": "series"}},
                        ],
                    }
                ],
            },
        )

        with self.assertRaises(DjangoValidationError):
            template.full_clean()


# ---------------------------------------------------------------------------
#  Workflow UI
# ---------------------------------------------------------------------------

class WorkflowUIViewTests(TestCase):

    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="runner",
            password="runner-pass",
        )
        self.client.force_login(self.user)
        self.factory = RequestFactory()

    def test_run_button_endpoint_queues_celery_and_redirects_to_run(self):
        wf = Workflow.objects.create(name="Runnable UI Workflow")
        _node(wf, WorkflowNode.LAMBDA, label="Start", lambda_fn=_lambda("ui_start"))

        with patch("toto.workflows.views.celery_available", return_value=True):
            with patch("toto.workflows.views.start_workflow_run_task.delay") as delay:
                response = self.client.post(reverse("workflows:workflow_run_start", args=[wf.id]))

        run = WorkflowRun.objects.get(workflow=wf)
        delay.assert_called_once_with(run.id)
        self.assertRedirects(
            response,
            reverse("workflows:workflow_run_detail", args=[run.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(run.status, WorkflowRun.PENDING)
        self.assertFalse(run.node_runs.exists())

    def test_run_button_endpoint_requires_celery(self):
        wf = Workflow.objects.create(name="Runnable UI Workflow")
        _node(wf, WorkflowNode.LAMBDA, label="Start", lambda_fn=_lambda("ui_start"))

        with patch("toto.workflows.views.celery_available", return_value=False):
            response = self.client.post(reverse("workflows:workflow_run_start", args=[wf.id]))

        self.assertRedirects(
            response,
            reverse("workflows:workflow_detail", args=[wf.id]),
            fetch_redirect_response=False,
        )
        self.assertFalse(WorkflowRun.objects.filter(workflow=wf).exists())

    def test_run_button_endpoint_rejects_invalid_workflow(self):
        wf = Workflow.objects.create(name="Empty UI Workflow")

        response = self.client.post(reverse("workflows:workflow_run_start", args=[wf.id]))

        self.assertRedirects(
            response,
            reverse("workflows:workflow_detail", args=[wf.id]),
            fetch_redirect_response=False,
        )
        self.assertFalse(WorkflowRun.objects.filter(workflow=wf).exists())

    def test_run_detail_displays_legacy_kernel_timeout_as_workflow_timeout(self):
        wf = Workflow.objects.create(name="Failed UI Workflow")
        node = _node(wf, WorkflowNode.LAMBDA, label="Build Trend Series", lambda_fn=_lambda("ui_fail"))
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.FAILED)
        WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=node,
            status=WorkflowNodeRun.FAILED,
            error="Kernel error: kernel_server_timeout",
        )

        from .views import WorkflowRunDetailUIView

        request = self.factory.get(f"/workflows/runs/{run.id}/")
        request.user = self.user
        view = WorkflowRunDetailUIView()
        view.request = request
        view.kwargs = {"run_id": run.id}
        view.object = run
        with patch("toto.workflows.views._decorate", lambda context, request: context):
            context = view.get_context_data(object=run)

        self.assertEqual(context["run_error"], "Workflow task timed out.")
        self.assertEqual(context["run_error_node"].label, "Build Trend Series")
        self.assertEqual(context["node_runs"][0].display_error, "Workflow task timed out.")

    def test_restart_completed_run_clones_input_and_redirects_to_new_run(self):
        wf = Workflow.objects.create(name="Restartable UI Workflow")
        _node(wf, WorkflowNode.LAMBDA, label="Start", lambda_fn=_lambda("ui_restart"))
        source_run = WorkflowRun.objects.create(
            workflow=wf,
            status=WorkflowRun.COMPLETED,
            input_data={"data": {"seed": 7}},
        )

        with patch("toto.workflows.views.celery_available", return_value=True):
            with patch("toto.workflows.views.start_workflow_run_task.delay") as delay:
                response = self.client.post(reverse("workflows:workflow_run_restart", args=[source_run.id]))

        new_run = WorkflowRun.objects.exclude(id=source_run.id).get(workflow=wf)
        delay.assert_called_once_with(new_run.id)
        self.assertRedirects(
            response,
            reverse("workflows:workflow_run_detail", args=[new_run.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(new_run.input_data, source_run.input_data)
        self.assertEqual(new_run.status, WorkflowRun.PENDING)
        self.assertFalse(new_run.node_runs.exists())

    def test_restart_requires_celery(self):
        wf = Workflow.objects.create(name="Restartable UI Workflow")
        _node(wf, WorkflowNode.LAMBDA, label="Start", lambda_fn=_lambda("ui_restart"))
        source_run = WorkflowRun.objects.create(
            workflow=wf,
            status=WorkflowRun.COMPLETED,
            input_data={"data": {"seed": 7}},
        )

        with patch("toto.workflows.views.celery_available", return_value=False):
            response = self.client.post(reverse("workflows:workflow_run_restart", args=[source_run.id]))

        self.assertRedirects(
            response,
            reverse("workflows:workflow_run_detail", args=[source_run.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(WorkflowRun.objects.filter(workflow=wf).count(), 1)

    def test_restart_rejects_non_completed_run(self):
        wf = Workflow.objects.create(name="Running UI Workflow")
        source_run = WorkflowRun.objects.create(
            workflow=wf,
            status=WorkflowRun.RUNNING,
            input_data={"data": {"seed": 7}},
        )

        response = self.client.post(reverse("workflows:workflow_run_restart", args=[source_run.id]))

        self.assertRedirects(
            response,
            reverse("workflows:workflow_run_detail", args=[source_run.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(WorkflowRun.objects.filter(workflow=wf).count(), 1)

    def test_human_task_submit_requires_celery(self):
        wf = Workflow.objects.create(name="Human UI Workflow")
        human_node = _node(
            wf,
            WorkflowNode.HUMAN,
            label="Review",
            config={"schema": {"type": "object", "properties": {"approved": {"type": "boolean"}}}},
        )
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.PAUSED)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=human_node,
            status=WorkflowNodeRun.WAITING,
        )
        task = HumanTask.objects.create(
            node_run=node_run,
            status=HumanTask.PENDING,
            form_schema=human_node.config["schema"],
        )

        with patch("toto.workflows.views.celery_available", return_value=False):
            response = self.client.post(
                reverse("workflows:api_human_task_submit", args=[task.id]),
                data={"submitted_data": {"approved": True}},
                content_type="application/json",
            )

        task.refresh_from_db()
        node_run.refresh_from_db()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(task.status, HumanTask.PENDING)
        self.assertEqual(node_run.status, WorkflowNodeRun.WAITING)

    def test_gate_submit_stores_trigger_inputs_files_and_redirects_to_run_detail(self):
        wf = Workflow.objects.create(name="Gate Workflow")
        trigger = _node(wf, WorkflowNode.TRIGGER, label="Manual Start")
        worker = _node(wf, WorkflowNode.LAMBDA, label="Worker", lambda_fn=_lambda("gate_worker"))
        _edge(wf, trigger, worker)
        WorkflowTriggerInput.objects.create(
            trigger_node=trigger,
            key="customer_id",
            label="Customer",
            input_type=WorkflowTriggerInput.TYPE_INT,
            required=True,
            order=1,
        )
        WorkflowTriggerInput.objects.create(
            trigger_node=trigger,
            key="threshold",
            label="Threshold",
            input_type=WorkflowTriggerInput.TYPE_FLOAT,
            order=2,
        )
        WorkflowTriggerInput.objects.create(
            trigger_node=trigger,
            key="notes",
            label="Notes",
            input_type=WorkflowTriggerInput.TYPE_TEXT,
            order=3,
        )
        WorkflowTriggerInput.objects.create(
            trigger_node=trigger,
            key="processing_deadline",
            label="Deadline",
            input_type=WorkflowTriggerInput.TYPE_DATETIME,
            required=True,
            order=4,
        )
        WorkflowTriggerInput.objects.create(
            trigger_node=trigger,
            key="source_documents",
            label="Documents",
            input_type=WorkflowTriggerInput.TYPE_FILE,
            required=True,
            allow_multiple_files=True,
            accepted_file_types="application/pdf",
            max_file_count=2,
            max_file_size=1024,
            order=5,
        )

        upload = SimpleUploadedFile(
            "invoice.pdf",
            b"%PDF-1.4 tiny",
            content_type="application/pdf",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            with override_settings(MEDIA_ROOT=tmpdir):
                with patch("toto.workflows.views.celery_available", return_value=True):
                    with patch("toto.workflows.views.start_workflow_run_task.delay") as delay:
                        response = self.client.post(
                            reverse("workflows:workflow_gate", args=[wf.id]),
                            data={
                                "customer_id": "123",
                                "threshold": "0.75",
                                "notes": "Run for May invoices",
                                "processing_deadline__date": "2026-05-21",
                                "processing_deadline__time": "14:30",
                                "source_documents": upload,
                            },
                        )

        run = WorkflowRun.objects.get(workflow=wf)
        delay.assert_called_once_with(run.id)
        self.assertRedirects(
            response,
            reverse("workflows:workflow_run_detail", args=[run.id]),
            fetch_redirect_response=False,
        )
        self.assertEqual(run.input_data["parameters"]["customer_id"], 123)
        self.assertEqual(run.input_data["parameters"]["threshold"], 0.75)
        self.assertIn("2026-05-21T14:30:00", run.input_data["parameters"]["processing_deadline"])
        self.assertEqual(run.input_data["files"]["source_documents"][0]["name"], "invoice.pdf")
        self.assertEqual(run.input_data["lambda_payload"]["workflow_id"], str(wf.id))
        self.assertEqual(run.input_data["lambda_payload"]["run_id"], str(run.id))
        self.assertEqual(run.input_data["lambda_payload"]["trigger_node_id"], str(trigger.id))

    def test_trigger_node_passes_manual_payload_to_downstream_lambda(self):
        fn = LambdaFunction.objects.create(
            function_name="trigger_payload_reader",
            content=(
                "import json\n"
                "payload = _input['data']\n"
                "print(json.dumps({'data': {'seen': payload['inputs']['customer_id'], "
                "'run_id': payload['run_id']}, 'route': 'end'}))"
            ),
        )
        wf = Workflow.objects.create(name="Trigger Executor")
        trigger = _node(wf, WorkflowNode.TRIGGER, label="Manual Start")
        worker = _node(wf, WorkflowNode.LAMBDA, label="Worker", lambda_fn=fn)
        _edge(wf, trigger, worker)
        run = WorkflowRun.objects.create(
            workflow=wf,
            input_data={
                "trigger_node_id": trigger.id,
                "parameters": {"customer_id": 123},
                "files": {},
                "inputs": {"customer_id": 123},
                "lambda_payload": {
                    "workflow_id": str(wf.id),
                    "run_id": "manual-run",
                    "trigger_node_id": str(trigger.id),
                    "inputs": {"customer_id": 123},
                },
            },
        )

        WorkflowExecutor().start(run)

        run.refresh_from_db()
        worker_run = run.node_runs.get(node=worker)
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(worker_run.input_data["data"]["inputs"]["customer_id"], 123)
        self.assertEqual(worker_run.output_data["data"], {"seen": 123, "run_id": "manual-run"})


# ---------------------------------------------------------------------------
#  Celery lambda node execution
# ---------------------------------------------------------------------------

class CeleryLambdaWorkflowTests(TestCase):

    def test_async_lambdas_queue_one_task_per_lambda_node(self):
        MockClient = _make_kernel_mock({"data": {"ok": True}, "route": "next"})
        wf = Workflow.objects.create(name="CeleryLinear")
        a = _node(wf, WorkflowNode.LAMBDA, label="A", lambda_fn=_lambda("celery_a"))
        b = _node(wf, WorkflowNode.LAMBDA, label="B", lambda_fn=_lambda("celery_b"))
        _edge(wf, a, b)

        run = WorkflowRun.objects.create(workflow=wf, input_data={"seed": 1})
        with override_settings(
            WORKFLOW_KERNEL_CLIENT=MockClient,
            CELERY_TASK_ALWAYS_EAGER=True,
            CELERY_TASK_EAGER_PROPAGATES=True,
        ):
            WorkflowExecutor(async_lambdas=True).start(run)

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(run.node_runs.filter(node__node_type=WorkflowNode.LAMBDA).count(), 2)
        self.assertEqual(run.node_runs.exclude(celery_task_id="").count(), 2)

    def test_lambda_task_executes_content_without_kernel_server(self):
        fn = LambdaFunction.objects.create(
            function_name="celery_local_lambda",
            content='import json\nprint(json.dumps({"data": {"ok": _input["data"]["ok"]}, "route": "end"}))',
        )
        wf = Workflow.objects.create(name="CeleryLocalLambda")
        _node(wf, WorkflowNode.LAMBDA, label="Local", lambda_fn=fn)

        run = WorkflowRun.objects.create(workflow=wf, input_data={"data": {"ok": True}})
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True):
            WorkflowExecutor(async_lambdas=True).start(run)

        run.refresh_from_db()
        node_run = run.node_runs.get()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        self.assertEqual(node_run.output_data["data"], {"ok": True})

    def test_lambda_task_failure_marks_node_and_run_failed(self):
        instance = MagicMock()
        instance.execute.return_value = {"error": "kernel_crash"}
        MockClient = type("MC", (), {"__new__": lambda cls, *a, **k: instance})

        wf = Workflow.objects.create(name="CeleryFailure")
        node = _node(wf, WorkflowNode.LAMBDA, label="bad", lambda_fn=_lambda("celery_fail"))
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.RUNNING)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=node,
            status=WorkflowNodeRun.PENDING,
            input_data={},
        )

        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            with self.assertRaises(RuntimeError):
                execute_lambda_node_task.apply(args=[node_run.id], throw=True)

        node_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.FAILED)
        self.assertIn("Workflow task error: kernel_crash", node_run.error)
        self.assertEqual(run.status, WorkflowRun.FAILED)

    def test_lambda_task_timeout_marks_node_and_run_failed(self):
        instance = MagicMock()
        instance.execute.side_effect = SoftTimeLimitExceeded()
        MockClient = type("MC", (), {"__new__": lambda cls, *a, **k: instance})

        wf = Workflow.objects.create(name="CeleryTimeout")
        node = _node(wf, WorkflowNode.LAMBDA, label="slow", lambda_fn=_lambda("celery_timeout"))
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.RUNNING)
        node_run = WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=node,
            status=WorkflowNodeRun.PENDING,
            input_data={},
        )

        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            with self.assertRaises(SoftTimeLimitExceeded):
                execute_lambda_node_task.apply(args=[node_run.id], throw=True)

        node_run.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(node_run.status, WorkflowNodeRun.FAILED)
        self.assertIn("timed out", node_run.error)
        self.assertEqual(run.status, WorkflowRun.FAILED)


# ---------------------------------------------------------------------------
#  Split / Join workflow
# ---------------------------------------------------------------------------

class SplitJoinWorkflowTests(TestCase):

    def setUp(self):
        self.wf = Workflow.objects.create(name="SplitJoin")
        self.fn_start = _lambda("start")
        self.fn_a = _lambda("branch_a")
        self.fn_b = _lambda("branch_b")
        self.fn_end = _lambda("end")

        self.n_start = _node(self.wf, WorkflowNode.LAMBDA, label="start", lambda_fn=self.fn_start)
        self.n_split = _node(self.wf, WorkflowNode.SPLIT, label="split")
        self.n_a = _node(self.wf, WorkflowNode.LAMBDA, label="branch_a", lambda_fn=self.fn_a)
        self.n_b = _node(self.wf, WorkflowNode.LAMBDA, label="branch_b", lambda_fn=self.fn_b)
        self.n_join = _node(self.wf, WorkflowNode.JOIN, label="join")
        self.n_end = _node(self.wf, WorkflowNode.LAMBDA, label="end", lambda_fn=self.fn_end)

        _edge(self.wf, self.n_start, self.n_split)
        self.e_a = _edge(self.wf, self.n_split, self.n_a, branch_key="path_a")
        self.e_b = _edge(self.wf, self.n_split, self.n_b, branch_key="path_b")
        _edge(self.wf, self.n_a, self.n_join)
        _edge(self.wf, self.n_b, self.n_join)
        _edge(self.wf, self.n_join, self.n_end)

    def _run(self, start_output: dict, branch_output: dict | None = None):
        branch_output = branch_output or {"data": {}, "routes": []}

        class SeqMockClient:
            def __init__(self):
                pass

            def execute(self, fn_id, code):
                if fn_id == SeqMockClient.fn_start_id:
                    return {"stdout": json.dumps(start_output)}
                return {"stdout": json.dumps(branch_output)}

        SeqMockClient.fn_start_id = self.fn_start.id

        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=SeqMockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        return run

    def test_both_routes_activated(self):
        run = self._run({"data": {}, "routes": ["path_a", "path_b"]})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        activated = WorkflowEdgeRun.objects.filter(workflow_run=run, activated=True)
        self.assertTrue(activated.filter(edge__branch_key="path_a").exists())
        self.assertTrue(activated.filter(edge__branch_key="path_b").exists())

    def test_single_route_skips_other_branch(self):
        run = self._run({"data": {}, "route": "path_a"})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        era = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_a)
        erb = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_b)
        self.assertTrue(era.activated)
        self.assertFalse(erb.activated)
        self.assertFalse(run.node_runs.filter(node=self.n_b).exists())

    def test_default_edge_fires_when_no_match(self):
        self.e_b.branch_key = ""
        self.e_b.is_default = True
        self.e_b.save()

        run = self._run({"data": {}, "route": "no_match"})
        self.assertEqual(run.status, WorkflowRun.COMPLETED)
        erb = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_b)
        self.assertTrue(erb.activated)


# ---------------------------------------------------------------------------
#  Human task
# ---------------------------------------------------------------------------

HUMAN_CONFIG = {
    "schema": {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean", "title": "Approve?"},
            "comment": {"type": "string", "title": "Comment"},
            "score_a": {"type": "number"},
            "score_b": {"type": "number"},
        },
        "required": ["approved"],
    },
    "output_mapping": {
        "route": {"field": "approved", "map": {"True": "approved_path", "False": "rejected_path"}},
        "data": {
            "comment": {"field": "comment"},
            "final_score": {"linear_combination": [
                {"field": "score_a", "weight": 0.6},
                {"field": "score_b", "weight": 0.4},
            ]},
        },
    },
}


class ApplyOutputMappingTests(TestCase):

    def test_route_via_field_map(self):
        result = apply_output_mapping({"approved": True, "comment": "looks good"}, HUMAN_CONFIG)
        self.assertEqual(result["routes"], ["approved_path"])
        self.assertEqual(result["data"]["comment"], "looks good")

    def test_route_rejected(self):
        result = apply_output_mapping({"approved": False}, HUMAN_CONFIG)
        self.assertEqual(result["routes"], ["rejected_path"])

    def test_linear_combination(self):
        result = apply_output_mapping({"approved": True, "score_a": 10, "score_b": 5}, HUMAN_CONFIG)
        self.assertAlmostEqual(result["data"]["final_score"], 8.0)

    def test_static_route(self):
        cfg = {"output_mapping": {"route": "always_this"}}
        result = apply_output_mapping({}, cfg)
        self.assertEqual(result["routes"], ["always_this"])

    def test_direct_field_route(self):
        cfg = {"output_mapping": {"route": {"field": "action"}}}
        result = apply_output_mapping({"action": "go_left"}, cfg)
        self.assertEqual(result["routes"], ["go_left"])

    def test_no_mapping_returns_empty(self):
        result = apply_output_mapping({"x": 1}, {})
        self.assertEqual(result["data"], {})
        self.assertEqual(result["routes"], [])


class HumanTaskWorkflowTests(TestCase):

    def setUp(self):
        self.wf = Workflow.objects.create(name="HumanWF")
        self.fn_start = _lambda("hs_start")
        self.fn_end = _lambda("hs_end")
        self.n_start = _node(self.wf, WorkflowNode.LAMBDA, label="start", lambda_fn=self.fn_start)
        self.n_human = _node(self.wf, WorkflowNode.HUMAN, label="human", config=HUMAN_CONFIG)
        self.n_end = _node(self.wf, WorkflowNode.LAMBDA, label="end", lambda_fn=self.fn_end)
        _edge(self.wf, self.n_start, self.n_human)
        self.e_approved = _edge(self.wf, self.n_human, self.n_end, branch_key="approved_path")

    def test_run_pauses_on_human(self):
        MockClient = _make_kernel_mock({"data": {}, "route": "next"})
        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.PAUSED)
        human_nr = run.node_runs.get(node=self.n_human)
        self.assertEqual(human_nr.status, WorkflowNodeRun.WAITING)
        self.assertTrue(HumanTask.objects.filter(node_run=human_nr).exists())

    def test_submit_resumes_and_completes(self):
        MockClient = _make_kernel_mock({"data": {}, "route": "next"})
        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)
        run.refresh_from_db()

        task = HumanTask.objects.get(node_run__workflow_run=run)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            submit_human_task(task, {"approved": True, "comment": "ok", "score_a": 8, "score_b": 6})

        run.refresh_from_db()
        self.assertEqual(run.status, WorkflowRun.COMPLETED)

        task.refresh_from_db()
        self.assertEqual(task.status, HumanTask.SUBMITTED)

        edge_run = WorkflowEdgeRun.objects.get(workflow_run=run, edge=self.e_approved)
        self.assertTrue(edge_run.activated)

    def test_submit_idempotent(self):
        MockClient = _make_kernel_mock({"data": {}, "route": "next"})
        run = WorkflowRun.objects.create(workflow=self.wf)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            WorkflowExecutor().start(run)

        task = HumanTask.objects.get(node_run__workflow_run=run)
        with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
            submit_human_task(task, {"approved": True})
            submit_human_task(task, {"approved": True})

        self.assertEqual(HumanTask.objects.filter(node_run__workflow_run=run).count(), 1)
