from pathlib import Path

from django.conf import settings
from toto.ingress import IngressCommand

from django.utils import timezone
from datetime import timedelta

from toto.api.models import Connector as ApiConnector
from toto.mandragora.models import (
    Cell, ComputeKernel, KernelDependency, Notebook,
)
from toto.workflows.services.reports import create_report, serialize_report_reference
from toto.workflows.models import (
    LambdaFunction,
    Report,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)

_DEFAULT_DEPS = [
    ("matplotlib", ""),
    ("numpy", ""),
    ("pandas", ""),
]


class Command(IngressCommand):
    help = "Seed default ComputeKernel and starter Notebook for Mandragora."

    def process(self):
        kernel, kernel_created = ComputeKernel.objects.get_or_create(
            name="Python 3",
            defaults={"timeout_ms": 30000, "env": {}},
        )
        if kernel_created:
            self.stdout.write(self.style.SUCCESS(f"Created kernel: {kernel.name}"))
            for pkg, ver in _DEFAULT_DEPS:
                KernelDependency.objects.get_or_create(
                    kernel=kernel,
                    package_name=pkg,
                    defaults={"version_spec": ver},
                )
                self.stdout.write(self.style.SUCCESS(f"  + dependency: {pkg}{ver or ''}"))
        else:
            self.stdout.write(self.style.WARNING(f"Kernel already exists: {kernel.name}"))

        notebook, nb_created = Notebook.objects.get_or_create(
            slug="getting-started",
            defaults={"title": "Getting Started", "kernel": kernel},
        )
        if nb_created:
            self.stdout.write(self.style.SUCCESS(f"Created notebook: {notebook.title}"))
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content='print("Hello from Mandragora!")',
                position=1,
            )
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content="import matplotlib.pyplot as plt\nimport numpy as np\n\nx = np.linspace(0, 2 * np.pi, 100)\nplt.plot(x, np.sin(x))\nplt.title('sine wave')\nplt.show()",
                position=2,
            )
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content="import sys\nprint(sys.version)",
                position=3,
            )
            Cell.objects.create(
                notebook=notebook,
                cell_type=Cell.CODE,
                content="""\
# files is available in every notebook cell — no import needed.
# It reads and writes inside WORKFLOW_FILE_CONNECTOR_ROOT (media/workflow-files/).

# Write a JSON file
files.write_json("notebook-demo/hello.json", {"message": "hello from notebook", "step": 1})

# Read it back
data = files.read_json("notebook-demo/hello.json")
print("read back:", data)

# Append a log line
files.append("notebook-demo/run.log", "notebook cell executed\\n")

# List directory
print("files in notebook-demo/:", files.list("notebook-demo"))
""",
                position=4,
            )
        else:
            self.stdout.write(self.style.WARNING(f"Notebook already exists: {notebook.title}"))

        self._seed_connectors()
        # Demo workflows + their sample run history and reports are illustrative
        # only — they pollute a minimal bring-up. Seed them solely under full
        # ingress (FULL_INGRESS=1). Functional workflows live in their own apps.
        if self.full:
            report_templates = self._seed_report_templates()
            self._seed_workflows(report_templates)
            self._seed_runs()
            self._seed_reports(report_templates)
        else:
            self.stdout.write(self.style.WARNING(
                "Skipping demo workflows/runs/reports (FULL_INGRESS=0)."
            ))

    # ------------------------------------------------------------------
    #  Connector seeds
    # ------------------------------------------------------------------

    def _seed_connectors(self):
        self._seed_connector_sample_file()
        self._seed_api_connector()
        self._seed_weather_api_connector()

    def _seed_connector_sample_file(self):
        root = getattr(settings, "WORKFLOW_FILE_CONNECTOR_ROOT", None)
        if root is None:
            root = Path(settings.MEDIA_ROOT) / "workflow-files"
        sample_path = Path(root) / "ingress" / "sample-input.json"
        sample_path.parent.mkdir(parents=True, exist_ok=True)
        if not sample_path.exists():
            sample_path.write_text(
                '{\n  "source": "mandragora-ingress",\n  "message": "Hello connector."\n}\n',
                encoding="utf-8",
            )

    def _seed_api_connector(self):
        api_connector, created = ApiConnector.objects.update_or_create(
            slug="httpbin-demo",
            defaults={
                "name": "HTTPBin Demo",
                "provider": ApiConnector.PROVIDER_GENERIC,
                "base_url": "https://httpbin.org/",
                "auth_type": ApiConnector.AUTH_NONE,
                "auth_config": {"timeout_seconds": 10},
                "extra": {
                    "headers": {"User-Agent": "toto-mandragora-ingress/1.0"},
                },
                "is_active": True,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("  + API connector: HTTPBin Demo"))
        return api_connector

    def _seed_weather_api_connector(self):
        api_connector, created = ApiConnector.objects.update_or_create(
            slug="open-meteo-forecast",
            defaults={
                "name": "Open-Meteo Forecast",
                "provider": ApiConnector.PROVIDER_GENERIC,
                "base_url": "https://api.open-meteo.com/",
                "auth_type": ApiConnector.AUTH_NONE,
                "auth_config": {"timeout_seconds": 10},
                "extra": {
                    "headers": {"User-Agent": "toto-mandragora-ingress/1.0"},
                },
                "is_active": True,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS("  + API connector: Open-Meteo Forecast"))
        return api_connector


    # ------------------------------------------------------------------
    #  Report seeds
    # ------------------------------------------------------------------

    def _seed_reports(self, templates=None):
        templates = templates or self._seed_report_templates()

        pipeline_report_run = WorkflowNodeRun.objects.filter(
            node__workflow__name="Data Pipeline",
            node__label="Output Report",
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        notify_run = pipeline_report_run or WorkflowNodeRun.objects.filter(
            node__workflow__name="Data Pipeline",
            node__label="Notify",
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        if notify_run:
            notify_data = (notify_run.input_data or {}).get("data", {})
            if not notify_data.get("rows"):
                notify_data = (notify_run.output_data or {}).get("data", {})
            rows = notify_data.get("rows") or [
                {"field": key, "value": value}
                for key, value in (notify_data.get("payload") or {}).items()
            ]
            self._seed_generated_report(
                slug="ingress-pipeline-output-table",
                template=templates["pipeline_table"],
                title="Pipeline Output Table",
                data={"rows": rows},
                source_node_run=notify_run,
            )

        enrichment_report_run = WorkflowNodeRun.objects.filter(
            node__workflow__name="Parallel Enrichment",
            node__label="Summary Chart",
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        enrichment_run = enrichment_report_run or WorkflowNodeRun.objects.filter(
            node__workflow__name="Parallel Enrichment",
            node__label="Summarise",
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        if enrichment_run:
            enrichment_data = (enrichment_run.input_data or {}).get("data", {})
            if not enrichment_data.get("series"):
                enrichment_data = (enrichment_run.output_data or {}).get("data", {})
            summary = enrichment_data.get("summary", {})
            series = enrichment_data.get("series") or self._summary_series(summary)
            self._seed_generated_report(
                slug="ingress-enrichment-chart",
                template=templates["enrichment_chart"],
                title="Enrichment Sections Chart",
                data={"series": series},
                source_node_run=enrichment_run,
            )

        self._seed_chart_example_report(
            slug="ingress-metrics-trend-line",
            workflow_name="Metrics Trend Report",
            report_node_label="Trend Line Report",
            source_node_label="Build Trend Series",
            template=templates["metrics_line"],
            title="Weekly Metrics Trend",
            data_key="series",
        )
        self._seed_chart_example_report(
            slug="ingress-channel-mix-pie",
            workflow_name="Channel Mix Report",
            report_node_label="Channel Mix Report",
            source_node_label="Build Channel Mix",
            template=templates["channel_pie"],
            title="Acquisition Channel Mix",
            data_key="series",
        )
        self._seed_weather_reports(templates)

    def _seed_report_templates(self):
        table, table_created = ReportTemplate.objects.update_or_create(
            slug="pipeline-output-table",
            defaults={
                "name": "Pipeline Output Table",
                "description": "One table report showing workflow output fields.",
                "definition": {
                    "version": 1,
                    "type": "table",
                    "title": "Output fields",
                    "data": {"path": "rows"},
                    "columns": [
                        {"key": "field", "label": "Field"},
                        {"key": "value", "label": "Value"},
                    ],
                },
            },
        )
        chart, chart_created = ReportTemplate.objects.update_or_create(
            slug="enrichment-sections-chart",
            defaults={
                "name": "Enrichment Sections Chart",
                "description": "One bar chart report showing enrichment section sizes.",
                "definition": {
                    "version": 1,
                    "type": "chart",
                    "title": "Sections",
                    "chart": "bar",
                    "data": {"path": "series"},
                    "x": "label",
                    "y": "value",
                },
            },
        )
        line, line_created = ReportTemplate.objects.update_or_create(
            slug="metrics-trend-line",
            defaults={
                "name": "Metrics Trend Line",
                "description": "One line chart report showing a fake weekly trend.",
                "definition": {
                    "version": 1,
                    "type": "chart",
                    "title": "Weekly active items",
                    "chart": "line",
                    "data": {"path": "series"},
                    "x": "label",
                    "y": "value",
                },
            },
        )
        pie, pie_created = ReportTemplate.objects.update_or_create(
            slug="channel-mix-pie",
            defaults={
                "name": "Channel Mix Pie",
                "description": "One pie chart report showing a fake acquisition split.",
                "definition": {
                    "version": 1,
                    "type": "chart",
                    "title": "Acquisition mix",
                    "chart": "pie",
                    "data": {"path": "series"},
                    "x": "label",
                    "y": "value",
                },
            },
        )
        weather_temperature, weather_temperature_created = ReportTemplate.objects.update_or_create(
            slug="weather-temperature-line",
            defaults={
                "name": "Weather Temperature Line",
                "description": "Daily maximum temperature forecast from Open-Meteo.",
                "definition": {
                    "version": 1,
                    "type": "chart",
                    "title": "Daily max temperature",
                    "chart": "line",
                    "data": {"path": "temperature_series"},
                    "x": "label",
                    "y": "value",
                },
            },
        )
        weather_precipitation, weather_precipitation_created = ReportTemplate.objects.update_or_create(
            slug="weather-precipitation-bar",
            defaults={
                "name": "Weather Precipitation Bar",
                "description": "Daily precipitation forecast from Open-Meteo.",
                "definition": {
                    "version": 1,
                    "type": "chart",
                    "title": "Daily precipitation",
                    "chart": "bar",
                    "data": {"path": "precipitation_series"},
                    "x": "label",
                    "y": "value",
                },
            },
        )
        if table_created:
            self.stdout.write(self.style.SUCCESS("  + report template: Pipeline Output Table"))
        if chart_created:
            self.stdout.write(self.style.SUCCESS("  + report template: Enrichment Sections Chart"))
        if line_created:
            self.stdout.write(self.style.SUCCESS("  + report template: Metrics Trend Line"))
        if pie_created:
            self.stdout.write(self.style.SUCCESS("  + report template: Channel Mix Pie"))
        if weather_temperature_created:
            self.stdout.write(self.style.SUCCESS("  + report template: Weather Temperature Line"))
        if weather_precipitation_created:
            self.stdout.write(self.style.SUCCESS("  + report template: Weather Precipitation Bar"))
        return {
            "pipeline_table": table,
            "enrichment_chart": chart,
            "metrics_line": line,
            "channel_pie": pie,
            "weather_temperature": weather_temperature,
            "weather_precipitation": weather_precipitation,
        }

    def _seed_weather_reports(self, templates):
        weather_run = WorkflowNodeRun.objects.filter(
            node__workflow__name="Weather Forecast Report",
            node__label="Build Weather Series",
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        if not weather_run:
            return
        weather_data = (weather_run.output_data or {}).get("data", {})
        self._seed_generated_report(
            slug="ingress-weather-temperature-line",
            template=templates["weather_temperature"],
            title="Poznan Temperature Forecast",
            data=weather_data,
            source_node_run=self._weather_report_node_run(weather_run, "Temperature Report") or weather_run,
        )
        self._seed_generated_report(
            slug="ingress-weather-precipitation-bar",
            template=templates["weather_precipitation"],
            title="Poznan Precipitation Forecast",
            data=weather_data,
            source_node_run=self._weather_report_node_run(weather_run, "Precipitation Report") or weather_run,
        )

    def _weather_report_node_run(self, source_run, report_label):
        return WorkflowNodeRun.objects.filter(
            workflow_run=source_run.workflow_run,
            node__label=report_label,
            status=WorkflowNodeRun.COMPLETED,
        ).first()

    def _seed_chart_example_report(
        self,
        *,
        slug,
        workflow_name,
        report_node_label,
        source_node_label,
        template,
        title,
        data_key,
    ):
        report_run = WorkflowNodeRun.objects.filter(
            node__workflow__name=workflow_name,
            node__label=report_node_label,
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        source_run = report_run or WorkflowNodeRun.objects.filter(
            node__workflow__name=workflow_name,
            node__label=source_node_label,
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("-completed_at").first()
        if not source_run:
            return
        data = (source_run.input_data or {}).get("data", {})
        if data_key not in data:
            data = (source_run.output_data or {}).get("data", {})
        self._seed_generated_report(
            slug=slug,
            template=template,
            title=title,
            data={data_key: data.get(data_key, [])},
            source_node_run=source_run,
        )

    def _seed_generated_report(self, *, slug, template, title, data, source_node_run):
        existing = Report.objects.filter(slug=slug).first()
        if existing:
            if source_node_run.node.node_type == WorkflowNode.REPORT:
                update_fields = []
                if existing.workflow_run_id != source_node_run.workflow_run_id:
                    existing.workflow_run = source_node_run.workflow_run
                    update_fields.append("workflow_run")
                if existing.source_node_run_id != source_node_run.id:
                    existing.source_node_run = source_node_run
                    update_fields.append("source_node_run")
                if update_fields:
                    existing.save(update_fields=[*update_fields, "updated_at"])
                self._update_report_node_run_output(source_node_run, existing)
            return
        report = create_report(
            template=template,
            data=data,
            title=title,
            workflow_run=source_node_run.workflow_run,
            source_node_run=source_node_run,
            metadata={"source": "mandragora-ingress"},
        )
        if report.slug != slug:
            report.slug = slug
            report.save(update_fields=["slug", "updated_at"])
        if source_node_run.node.node_type == WorkflowNode.REPORT:
            self._update_report_node_run_output(source_node_run, report)
        self.stdout.write(self.style.SUCCESS(f"  + report: {title}"))

    def _update_report_node_run_output(self, node_run, report):
        node_run.output_data = {
            "data": {"report": serialize_report_reference(report)},
            "routes": (node_run.output_data or {}).get("routes", ["end"]),
        }
        node_run.save(update_fields=["output_data"])

    def _ensure_seed_report_node_run(self, *, workflow_run, node, input_data, title, completed_at):
        completed_at = completed_at or workflow_run.completed_at or timezone.now()
        node_run, created = WorkflowNodeRun.objects.get_or_create(
            workflow_run=workflow_run,
            node=node,
            defaults={
                "status": WorkflowNodeRun.COMPLETED,
                "input_data": input_data,
                "output_data": {"data": {"report": {"title": title}}, "routes": ["end"]},
                "started_at": completed_at,
                "completed_at": completed_at,
            },
        )
        return node_run, created

    def _ensure_seed_report_edge_run(self, *, workflow_run, edge, activated_at):
        if edge is None:
            return
        edge_run, created = WorkflowEdgeRun.objects.get_or_create(
            workflow_run=workflow_run,
            edge=edge,
            defaults={
                "activated": True,
                "activated_at": activated_at or timezone.now(),
            },
        )
        if not created and not edge_run.activated:
            edge_run.activated = True
            edge_run.activated_at = activated_at or timezone.now()
            edge_run.save(update_fields=["activated", "activated_at"])

    def _summary_series(self, summary):
        return [
            {"label": key.replace("_", " ").title(), "value": len(value) if isinstance(value, dict) else 1}
            for key, value in summary.items()
        ]

    # ------------------------------------------------------------------
    #  Workflow seeds
    # ------------------------------------------------------------------

    def _seed_workflows(self, report_templates=None):
        report_templates = report_templates or self._seed_report_templates()
        self._seed_linear_workflow(report_templates["pipeline_table"])
        self._seed_approval_workflow()
        self._seed_fanout_workflow(report_templates["enrichment_chart"])
        self._seed_line_chart_workflow(report_templates["metrics_line"])
        self._seed_pie_chart_workflow(report_templates["channel_pie"])
        self._seed_weather_workflow(
            report_templates["weather_temperature"],
            report_templates["weather_precipitation"],
        )
        self._seed_file_client_workflow(report_templates["pipeline_table"])

    def _upsert_lambda(self, name: str, src: str) -> LambdaFunction:
        fn, created = LambdaFunction.objects.update_or_create(
            function_name=name,
            defaults={"content": src},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"  + lambda: {name}"))
        return fn

    def _seed_linear_workflow(self, report_template=None):
        """validate → transform → notify (3 lambda nodes in sequence)."""
        wf, created = Workflow.objects.get_or_create(
            name="Data Pipeline",
            defaults={"description": "Validate, transform and notify — a simple 3-step linear pipeline."},
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: Data Pipeline"))
            if report_template:
                self._ensure_linear_report_node(wf, report_template)
            return

        fn_validate = self._upsert_lambda("pipeline_validate", """\
import json
data = _input.get("data", {})
valid = bool(data)
print(json.dumps({"data": {"valid": valid, "raw": data}, "route": "ok" if valid else "error"}))
""")
        fn_transform = self._upsert_lambda("pipeline_transform", """\
import json
raw = _input.get("data", {}).get("raw", {})
transformed = {k: str(v).upper() for k, v in raw.items()}
print(json.dumps({"data": {"result": transformed}, "route": "done"}))
""")
        fn_notify = self._upsert_lambda("pipeline_notify", """\
import json
result = _input.get("data", {}).get("result", {})
rows = [{"field": k, "value": v} for k, v in result.items()]
print(json.dumps({"data": {"notified": True, "payload": result, "rows": rows}, "route": "end"}))
""")

        n1 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Validate",  lambda_function=fn_validate,  position_x=0,   position_y=0)
        n2 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Transform", lambda_function=fn_transform, position_x=0,   position_y=120)
        n3 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Notify",    lambda_function=fn_notify,    position_x=0,   position_y=240)
        WorkflowEdge.objects.create(workflow=wf, source=n1, target=n2)
        WorkflowEdge.objects.create(workflow=wf, source=n2, target=n3)
        if report_template:
            n_report = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.REPORT,
                label="Output Report",
                report_template=report_template,
                config={"title": "Pipeline Output Table", "route": "end"},
                position_x=0,
                position_y=360,
            )
            WorkflowEdge.objects.create(workflow=wf, source=n3, target=n_report)
        self.stdout.write(self.style.SUCCESS("Created workflow: Data Pipeline"))

    def _ensure_linear_report_node(self, wf, report_template):
        notify = wf.nodes.filter(label="Notify").first()
        if notify is None:
            return
        report_node, created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label="Output Report",
            defaults={
                "node_type": WorkflowNode.REPORT,
                "report_template": report_template,
                "config": {"title": "Pipeline Output Table", "route": "end"},
                "position_x": 0,
                "position_y": 360,
            },
        )
        changed = False
        if report_node.node_type != WorkflowNode.REPORT:
            report_node.node_type = WorkflowNode.REPORT
            changed = True
        if report_node.report_template_id != report_template.id:
            report_node.report_template = report_template
            changed = True
        if changed:
            report_node.save(update_fields=["node_type", "report_template"])
        WorkflowEdge.objects.get_or_create(workflow=wf, source=notify, target=report_node)
        if created:
            self.stdout.write(self.style.SUCCESS("  + report node: Output Report"))

    def _seed_approval_workflow(self):
        """score → human review (approve/reject) → approve branch or reject branch."""
        wf, created = Workflow.objects.get_or_create(
            name="Human Approval Gate",
            defaults={"description": "Score an item automatically, then route through a human approval step."},
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: Human Approval Gate"))
            return

        fn_score = self._upsert_lambda("approval_score", """\
import json, random
score = round(random.uniform(0, 100), 2)
print(json.dumps({"data": {"score": score, "item_id": _input.get("data", {}).get("item_id", 1)}, "route": "review"}))
""")
        fn_accept = self._upsert_lambda("approval_accept", """\
import json
print(json.dumps({"data": {"decision": "accepted", "score": _input.get("data", {}).get("score")}, "route": "end"}))
""")
        fn_reject = self._upsert_lambda("approval_reject", """\
import json
print(json.dumps({"data": {"decision": "rejected", "score": _input.get("data", {}).get("score")}, "route": "end"}))
""")

        human_config = {
            "schema": {
                "type": "object",
                "properties": {
                    "approved": {"type": "boolean", "title": "Approve this item?"},
                    "comment":  {"type": "string",  "title": "Reviewer comment"},
                },
                "required": ["approved"],
            },
            "output_mapping": {
                "route": {"field": "approved", "map": {"True": "approved", "False": "rejected"}},
                "data": {"comment": {"field": "comment"}},
            },
        }

        n_score  = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Score",   lambda_function=fn_score,  position_x=0,    position_y=0)
        n_human  = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Review",  config=human_config,        position_x=0,    position_y=120)
        n_accept = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Accept",  lambda_function=fn_accept, position_x=-150, position_y=240)
        n_reject = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Reject",  lambda_function=fn_reject, position_x=150,  position_y=240)
        WorkflowEdge.objects.create(workflow=wf, source=n_score,  target=n_human)
        WorkflowEdge.objects.create(workflow=wf, source=n_human,  target=n_accept, branch_key="approved")
        WorkflowEdge.objects.create(workflow=wf, source=n_human,  target=n_reject, branch_key="rejected")
        self.stdout.write(self.style.SUCCESS("Created workflow: Human Approval Gate"))

    def _seed_fanout_workflow(self, report_template=None):
        """fetch → split into 3 parallel enrichment lambdas → join → summarise."""
        wf, created = Workflow.objects.get_or_create(
            name="Parallel Enrichment",
            defaults={"description": "Fan out to three enrichment lambdas in parallel, then merge and summarise."},
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: Parallel Enrichment"))
            if report_template:
                self._ensure_enrichment_report_node(wf, report_template)
            return

        fn_fetch = self._upsert_lambda("enrich_fetch", """\
import json
print(json.dumps({"data": {"entity": _input.get("data", {}).get("entity", "unknown")}, "routes": ["geo", "financial", "social"]}))
""")
        fn_geo = self._upsert_lambda("enrich_geo", """\
import json
print(json.dumps({"data": {"geo": {"country": "PL", "city": "Poznań"}}, "route": "merged"}))
""")
        fn_fin = self._upsert_lambda("enrich_financial", """\
import json
print(json.dumps({"data": {"financial": {"credit_score": 720}}, "route": "merged"}))
""")
        fn_soc = self._upsert_lambda("enrich_social", """\
import json
print(json.dumps({"data": {"social": {"followers": 1024}}, "route": "merged"}))
""")
        fn_summarise = self._upsert_lambda("enrich_summarise", """\
import json
d = _input.get("data", {})
series = [{"label": k.replace("_", " ").title(), "value": len(v) if isinstance(v, dict) else 1} for k, v in d.items()]
print(json.dumps({"data": {"summary": d, "series": series, "complete": True}, "route": "end"}))
""")

        n_fetch     = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Fetch",      lambda_function=fn_fetch,     position_x=0,    position_y=0)
        n_split     = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.SPLIT,  label="Fan-out",                                  position_x=0,    position_y=100)
        n_geo       = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Geo",        lambda_function=fn_geo,       position_x=-200, position_y=200)
        n_fin       = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Financial",  lambda_function=fn_fin,       position_x=0,    position_y=200)
        n_soc       = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Social",     lambda_function=fn_soc,       position_x=200,  position_y=200)
        n_join      = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.JOIN,   label="Merge",                                    position_x=0,    position_y=300)
        n_summarise = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Summarise",  lambda_function=fn_summarise, position_x=0,    position_y=400)
        n_report = None
        if report_template:
            n_report = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.REPORT,
                label="Summary Chart",
                report_template=report_template,
                config={"title": "Enrichment Sections Chart", "route": "end"},
                position_x=0,
                position_y=520,
            )
        WorkflowEdge.objects.create(workflow=wf, source=n_fetch,     target=n_split)
        WorkflowEdge.objects.create(workflow=wf, source=n_split,     target=n_geo,       branch_key="geo")
        WorkflowEdge.objects.create(workflow=wf, source=n_split,     target=n_fin,       branch_key="financial")
        WorkflowEdge.objects.create(workflow=wf, source=n_split,     target=n_soc,       branch_key="social")
        WorkflowEdge.objects.create(workflow=wf, source=n_geo,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_fin,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_soc,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_join,      target=n_summarise)
        if n_report:
            WorkflowEdge.objects.create(workflow=wf, source=n_summarise, target=n_report)
        self.stdout.write(self.style.SUCCESS("Created workflow: Parallel Enrichment"))

    def _ensure_enrichment_report_node(self, wf, report_template):
        summarise = wf.nodes.filter(label="Summarise").first()
        if summarise is None:
            return
        report_node, created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label="Summary Chart",
            defaults={
                "node_type": WorkflowNode.REPORT,
                "report_template": report_template,
                "config": {"title": "Enrichment Sections Chart", "route": "end"},
                "position_x": 0,
                "position_y": 520,
            },
        )
        changed = False
        if report_node.node_type != WorkflowNode.REPORT:
            report_node.node_type = WorkflowNode.REPORT
            changed = True
        if report_node.report_template_id != report_template.id:
            report_node.report_template = report_template
            changed = True
        if changed:
            report_node.save(update_fields=["node_type", "report_template"])
        WorkflowEdge.objects.get_or_create(workflow=wf, source=summarise, target=report_node)
        if created:
            self.stdout.write(self.style.SUCCESS("  + report node: Summary Chart"))

    def _seed_line_chart_workflow(self, report_template=None):
        self._seed_single_report_workflow(
            name="Metrics Trend Report",
            description="Fake backend flow that creates a line chart report from synthetic weekly metrics.",
            lambda_name="report_metrics_trend",
            lambda_label="Build Trend Series",
            lambda_source="""\
import json
series = [
    {"label": "Mon", "value": 42},
    {"label": "Tue", "value": 51},
    {"label": "Wed", "value": 48},
    {"label": "Thu", "value": 64},
    {"label": "Fri", "value": 73},
    {"label": "Sat", "value": 69},
    {"label": "Sun", "value": 81},
]
print(json.dumps({"data": {"series": series}, "route": "report"}))
""",
            report_label="Trend Line Report",
            report_template=report_template,
            report_title="Weekly Metrics Trend",
        )

    def _seed_pie_chart_workflow(self, report_template=None):
        self._seed_single_report_workflow(
            name="Channel Mix Report",
            description="Fake backend flow that creates a pie chart report from synthetic channel mix data.",
            lambda_name="report_channel_mix",
            lambda_label="Build Channel Mix",
            lambda_source="""\
import json
series = [
    {"label": "Organic", "value": 44},
    {"label": "Social", "value": 27},
    {"label": "Referral", "value": 16},
    {"label": "Paid", "value": 13},
]
print(json.dumps({"data": {"series": series}, "route": "report"}))
""",
            report_label="Channel Mix Report",
            report_template=report_template,
            report_title="Acquisition Channel Mix",
        )

    def _seed_weather_workflow(self, temperature_template=None, precipitation_template=None):
        self.stdout.write(self.style.WARNING("Skipping Weather Forecast workflow — CONNECTOR/TRIGGER node types removed in spine redesign"))
        return
        fn_request = self._upsert_lambda("weather_build_request", """\
import json

inputs = _input.get("data", {}).get("inputs", {})
latitude = float(inputs.get("latitude") or 52.4069)
longitude = float(inputs.get("longitude") or 16.9252)
forecast_days = int(inputs.get("forecast_days") or 7)
city = inputs.get("city") or "Poznan"
params = {
    "latitude": latitude,
    "longitude": longitude,
    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
    "forecast_days": max(1, min(forecast_days, 16)),
    "timezone": "Europe/Warsaw",
}
print(json.dumps({"data": {"city": city, "params": params}, "route": "fetch"}))
""")
        fn_series = self._upsert_lambda("weather_build_series", """\
import json

payload = _input.get("data", {}).get("json", {})
city = _input.get("data", {}).get("city", "Poznan")
daily = payload.get("daily", {})
units = payload.get("daily_units", {})
dates = daily.get("time", [])
max_t = daily.get("temperature_2m_max", [])
min_t = daily.get("temperature_2m_min", [])
precip = daily.get("precipitation_sum", [])

temperature_series = []
precipitation_series = []
for index, label in enumerate(dates):
    if index < len(max_t) and max_t[index] is not None:
        temperature_series.append({
            "label": label,
            "value": max_t[index],
            "min": min_t[index] if index < len(min_t) else None,
        })
    if index < len(precip) and precip[index] is not None:
        precipitation_series.append({
            "label": label,
            "value": precip[index],
        })

print(json.dumps({
    "data": {
        "location": city,
        "source": "Open-Meteo",
        "temperature_unit": units.get("temperature_2m_max", "degC"),
        "precipitation_unit": units.get("precipitation_sum", "mm"),
        "temperature_series": temperature_series,
        "precipitation_series": precipitation_series,
    },
    "route": "report",
}))
""")
        wf, created = Workflow.objects.get_or_create(
            name="Weather Forecast Report",
            defaults={
                "description": (
                    "Fetch a 7 day Open-Meteo forecast and route daily temperature "
                    "and precipitation into separate reports."
                )
            },
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: Weather Forecast Report"))
            self._ensure_weather_workflow_nodes(
                wf=wf,
                connector=connector,
                request_lambda=fn_request,
                series_lambda=fn_series,
                temperature_template=temperature_template,
                precipitation_template=precipitation_template,
            )
            return

        n_trigger = WorkflowNode.objects.create(
            workflow=wf,
            node_type=WorkflowNode.TRIGGER,
            label="Weather Gate",
            position_x=0,
            position_y=0,
        )
        self._ensure_weather_trigger_inputs(n_trigger)
        n_request = WorkflowNode.objects.create(
            workflow=wf,
            node_type=WorkflowNode.LAMBDA,
            label="Build Forecast Request",
            lambda_function=fn_request,
            position_x=0,
            position_y=120,
        )
        n_fetch = WorkflowNode.objects.create(
            workflow=wf,
            node_type=WorkflowNode.CONNECTOR,
            label="Fetch Forecast",
            connector=connector,
            position_x=0,
            position_y=240,
        )
        n_series = WorkflowNode.objects.create(
            workflow=wf,
            node_type=WorkflowNode.LAMBDA,
            label="Build Weather Series",
            lambda_function=fn_series,
            position_x=0,
            position_y=360,
        )
        WorkflowEdge.objects.create(workflow=wf, source=n_trigger, target=n_request)
        WorkflowEdge.objects.create(workflow=wf, source=n_request, target=n_fetch)
        WorkflowEdge.objects.create(workflow=wf, source=n_fetch, target=n_series)
        if temperature_template:
            n_temperature = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.REPORT,
                label="Temperature Report",
                report_template=temperature_template,
                config={"title": "Poznan Temperature Forecast", "route": "end"},
                position_x=-160,
                position_y=500,
            )
            WorkflowEdge.objects.create(workflow=wf, source=n_series, target=n_temperature)
        if precipitation_template:
            n_precipitation = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.REPORT,
                label="Precipitation Report",
                report_template=precipitation_template,
                config={"title": "Poznan Precipitation Forecast", "route": "end"},
                position_x=160,
                position_y=500,
            )
            WorkflowEdge.objects.create(workflow=wf, source=n_series, target=n_precipitation)
        self.stdout.write(self.style.SUCCESS("Created workflow: Weather Forecast Report"))

    def _ensure_weather_workflow_nodes(
        self,
        *,
        wf,
        connector,
        request_lambda,
        series_lambda,
        temperature_template,
        precipitation_template,
    ):
        trigger, trigger_created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label="Weather Gate",
            defaults={
                "node_type": WorkflowNode.TRIGGER,
                "position_x": 0,
                "position_y": 0,
            },
        )
        if trigger.node_type != WorkflowNode.TRIGGER:
            trigger.node_type = WorkflowNode.TRIGGER
        if trigger.position_x != 0 or trigger.position_y != 0:
            trigger.position_x = 0
            trigger.position_y = 0
        trigger.save(update_fields=["node_type", "position_x", "position_y"])
        self._ensure_weather_trigger_inputs(trigger)
        if trigger_created:
            self.stdout.write(self.style.SUCCESS("  + trigger node: Weather Gate"))

        request_node, request_created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label="Build Forecast Request",
            defaults={
                "node_type": WorkflowNode.LAMBDA,
                "lambda_function": request_lambda,
                "position_x": 0,
                "position_y": 120,
            },
        )
        request_changed = False
        if request_node.node_type != WorkflowNode.LAMBDA:
            request_node.node_type = WorkflowNode.LAMBDA
            request_changed = True
        if request_node.lambda_function_id != request_lambda.id:
            request_node.lambda_function = request_lambda
            request_changed = True
        if request_node.position_x != 0 or request_node.position_y != 120:
            request_node.position_x = 0
            request_node.position_y = 120
            request_changed = True
        if request_changed:
            request_node.save(update_fields=["node_type", "lambda_function", "position_x", "position_y"])
        WorkflowEdge.objects.get_or_create(workflow=wf, source=trigger, target=request_node)
        if request_created:
            self.stdout.write(self.style.SUCCESS("  + lambda node: Build Forecast Request"))

        fetch, fetch_created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label="Fetch Forecast",
            defaults={
                "node_type": WorkflowNode.CONNECTOR,
                "connector": connector,
                "position_x": 0,
                "position_y": 240,
            },
        )
        fetch_changed = False
        if fetch.node_type != WorkflowNode.CONNECTOR:
            fetch.node_type = WorkflowNode.CONNECTOR
            fetch_changed = True
        if connector and fetch.connector_id != connector.id:
            fetch.connector = connector
            fetch_changed = True
        if fetch.position_x != 0 or fetch.position_y != 240:
            fetch.position_x = 0
            fetch.position_y = 240
            fetch_changed = True
        if fetch_changed:
            fetch.save(update_fields=["node_type", "connector", "position_x", "position_y"])
        if fetch_created:
            self.stdout.write(self.style.SUCCESS("  + connector node: Fetch Forecast"))
        WorkflowEdge.objects.get_or_create(workflow=wf, source=request_node, target=fetch)

        series, series_created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label="Build Weather Series",
            defaults={
                "node_type": WorkflowNode.LAMBDA,
                "lambda_function": series_lambda,
                "position_x": 0,
                "position_y": 360,
            },
        )
        series_changed = False
        if series.node_type != WorkflowNode.LAMBDA:
            series.node_type = WorkflowNode.LAMBDA
            series_changed = True
        if series.lambda_function_id != series_lambda.id:
            series.lambda_function = series_lambda
            series_changed = True
        if series.position_x != 0 or series.position_y != 360:
            series.position_x = 0
            series.position_y = 360
            series_changed = True
        if series_changed:
            series.save(update_fields=["node_type", "lambda_function", "position_x", "position_y"])
        WorkflowEdge.objects.get_or_create(workflow=wf, source=fetch, target=series)
        if series_created:
            self.stdout.write(self.style.SUCCESS("  + lambda node: Build Weather Series"))

        self._ensure_weather_report_node(
            wf=wf,
            source=series,
            label="Temperature Report",
            template=temperature_template,
            title="Poznan Temperature Forecast",
            position_x=-160,
        )
        self._ensure_weather_report_node(
            wf=wf,
            source=series,
            label="Precipitation Report",
            template=precipitation_template,
            title="Poznan Precipitation Forecast",
            position_x=160,
        )

    def _ensure_weather_trigger_inputs(self, trigger_node):
        pass  # WorkflowTriggerInput removed in spine redesign

    def _ensure_weather_report_node(self, *, wf, source, label, template, title, position_x):
        if not template:
            return
        report_node, created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label=label,
            defaults={
                "node_type": WorkflowNode.REPORT,
                "report_template": template,
                "config": {"title": title, "route": "end"},
                "position_x": position_x,
                "position_y": 500,
            },
        )
        changed = False
        if report_node.node_type != WorkflowNode.REPORT:
            report_node.node_type = WorkflowNode.REPORT
            changed = True
        if report_node.report_template_id != template.id:
            report_node.report_template = template
            changed = True
        if report_node.position_x != position_x or report_node.position_y != 500:
            report_node.position_x = position_x
            report_node.position_y = 500
            changed = True
        if changed:
            report_node.save(update_fields=["node_type", "report_template", "position_x", "position_y"])
        WorkflowEdge.objects.get_or_create(workflow=wf, source=source, target=report_node)
        if created:
            self.stdout.write(self.style.SUCCESS(f"  + report node: {label}"))

    def _seed_single_report_workflow(
        self,
        *,
        name,
        description,
        lambda_name,
        lambda_label,
        lambda_source,
        report_label,
        report_template,
        report_title,
    ):
        fn = self._upsert_lambda(lambda_name, lambda_source)
        wf, created = Workflow.objects.get_or_create(
            name=name,
            defaults={"description": description},
        )
        if not created:
            self.stdout.write(self.style.WARNING(f"Workflow already exists: {name}"))
            self._ensure_single_report_workflow_nodes(
                wf=wf,
                lambda_label=lambda_label,
                lambda_function=fn,
                report_label=report_label,
                report_template=report_template,
                report_title=report_title,
            )
            return

        n_source = WorkflowNode.objects.create(
            workflow=wf,
            node_type=WorkflowNode.LAMBDA,
            label=lambda_label,
            lambda_function=fn,
            position_x=0,
            position_y=0,
        )
        if report_template:
            n_report = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.REPORT,
                label=report_label,
                report_template=report_template,
                config={"title": report_title, "route": "end"},
                position_x=0,
                position_y=120,
            )
            WorkflowEdge.objects.create(workflow=wf, source=n_source, target=n_report)
        self.stdout.write(self.style.SUCCESS(f"Created workflow: {name}"))

    def _ensure_single_report_workflow_nodes(
        self,
        *,
        wf,
        lambda_label,
        lambda_function,
        report_label,
        report_template,
        report_title,
    ):
        source_node, source_created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label=lambda_label,
            defaults={
                "node_type": WorkflowNode.LAMBDA,
                "lambda_function": lambda_function,
                "position_x": 0,
                "position_y": 0,
            },
        )
        source_changed = False
        if source_node.node_type != WorkflowNode.LAMBDA:
            source_node.node_type = WorkflowNode.LAMBDA
            source_changed = True
        if source_node.lambda_function_id != lambda_function.id:
            source_node.lambda_function = lambda_function
            source_changed = True
        if source_changed:
            source_node.save(update_fields=["node_type", "lambda_function"])
        if source_created:
            self.stdout.write(self.style.SUCCESS(f"  + lambda node: {lambda_label}"))

        if not report_template:
            return
        report_node, report_created = WorkflowNode.objects.get_or_create(
            workflow=wf,
            label=report_label,
            defaults={
                "node_type": WorkflowNode.REPORT,
                "report_template": report_template,
                "config": {"title": report_title, "route": "end"},
                "position_x": 0,
                "position_y": 120,
            },
        )
        report_changed = False
        if report_node.node_type != WorkflowNode.REPORT:
            report_node.node_type = WorkflowNode.REPORT
            report_changed = True
        if report_node.report_template_id != report_template.id:
            report_node.report_template = report_template
            report_changed = True
        if report_changed:
            report_node.save(update_fields=["node_type", "report_template"])
        WorkflowEdge.objects.get_or_create(workflow=wf, source=source_node, target=report_node)
        if report_created:
            self.stdout.write(self.style.SUCCESS(f"  + report node: {report_label}"))

    def _seed_file_client_workflow(self, report_template=None):
        """write → read → summarise: demonstrates files.write / files.read in lambdas."""
        wf, created = Workflow.objects.get_or_create(
            name="File Client Demo",
            defaults={"description": "Write data to a file, read it back, and summarise — shows files.write_json / files.read_json inside lambda nodes."},
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: File Client Demo"))
            return

        fn_write = self._upsert_lambda("file_demo_write", """\
import json

payload = _input.get("data", {}).get("payload", {"demo": True, "value": 42})
files.write_json("workflow-demo/input.json", payload)
print(json.dumps({"data": {"written": True, "path": "workflow-demo/input.json", "payload": payload}, "route": "read"}))
""")
        fn_read = self._upsert_lambda("file_demo_read", """\
import json

data = files.read_json("workflow-demo/input.json")
files.append("workflow-demo/run.log", "read step executed\\n")
print(json.dumps({"data": {"read": data, "log_path": "workflow-demo/run.log"}, "route": "summarise"}))
""")
        fn_summarise = self._upsert_lambda("file_demo_summarise", """\
import json

read_data = _input.get("data", {}).get("read", {})
rows = [{"field": k, "value": str(v)} for k, v in read_data.items()]
files.write_json("workflow-demo/output.json", {"rows": rows, "count": len(rows)})
print(json.dumps({"data": {"rows": rows, "output_path": "workflow-demo/output.json"}, "route": "end"}))
""")

        n1 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Write File",   lambda_function=fn_write,     position_x=0, position_y=0)
        n2 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Read File",    lambda_function=fn_read,      position_x=0, position_y=120)
        n3 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Summarise",    lambda_function=fn_summarise, position_x=0, position_y=240)
        WorkflowEdge.objects.create(workflow=wf, source=n1, target=n2)
        WorkflowEdge.objects.create(workflow=wf, source=n2, target=n3)
        if report_template:
            n_report = WorkflowNode.objects.create(
                workflow=wf,
                node_type=WorkflowNode.REPORT,
                label="File Output Report",
                report_template=report_template,
                config={"title": "File Client Demo Output", "route": "end"},
                position_x=0,
                position_y=360,
            )
            WorkflowEdge.objects.create(workflow=wf, source=n3, target=n_report)
        self.stdout.write(self.style.SUCCESS("Created workflow: File Client Demo"))

    # ------------------------------------------------------------------
    #  Fake run seeds
    # ------------------------------------------------------------------

    def _seed_runs(self):
        now = timezone.now()

        self._seed_pipeline_runs(now)
        self._seed_approval_runs(now)
        self._seed_enrichment_runs(now)
        self._seed_metrics_trend_runs(now)
        self._seed_channel_mix_runs(now)
        self._seed_weather_runs(now)
        self._seed_file_client_runs(now)

    def _nr(self, run, node, status, input_data, output_data, ago_start, ago_end=None, error=""):
        """Create a WorkflowNodeRun with realistic timestamps."""
        now = timezone.now()
        started = now - ago_start
        completed = (now - ago_end) if ago_end is not None else None
        return WorkflowNodeRun.objects.create(
            workflow_run=run,
            node=node,
            status=status,
            input_data=input_data,
            output_data=output_data,
            error=error,
            started_at=started,
            completed_at=completed,
        )

    def _er(self, run, edge, activated, ago=None):
        now = timezone.now()
        return WorkflowEdgeRun.objects.create(
            workflow_run=run,
            edge=edge,
            activated=activated,
            activated_at=(now - ago) if (activated and ago) else None,
        )

    # --- Data Pipeline ---

    def _seed_pipeline_runs(self, now):
        try:
            wf = Workflow.objects.get(name="Data Pipeline")
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING("Runs already exist: Data Pipeline"))
            self._backfill_pipeline_report_runs(wf)
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = list(wf.edges.select_related("source", "target").all())

        # Run 1 — completed, 2 hours ago
        r1 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"name": "Alice", "score": 98}},
            output_data={"data": {"notified": True}, "routes": ["end"]},
            started_at=now - timedelta(hours=2, minutes=5),
            completed_at=now - timedelta(hours=2),
        )
        validate_out = {"data": {"valid": True, "raw": {"name": "Alice", "score": 98}}, "routes": ["ok"]}
        transform_out = {"data": {"result": {"NAME": "ALICE", "SCORE": "98"}}, "routes": ["done"]}
        notify_out = {
            "data": {
                "notified": True,
                "payload": {"NAME": "ALICE", "SCORE": "98"},
                "rows": [
                    {"field": "NAME", "value": "ALICE"},
                    {"field": "SCORE", "value": "98"},
                ],
            },
            "routes": ["end"],
        }
        self._nr(r1, nodes["Validate"],  WorkflowNodeRun.COMPLETED, r1.input_data,  validate_out,  timedelta(hours=2, minutes=5), timedelta(hours=2, minutes=4))
        self._nr(r1, nodes["Transform"], WorkflowNodeRun.COMPLETED, validate_out,   transform_out, timedelta(hours=2, minutes=4), timedelta(hours=2, minutes=3))
        self._nr(r1, nodes["Notify"],    WorkflowNodeRun.COMPLETED, transform_out,  notify_out,    timedelta(hours=2, minutes=3), timedelta(hours=2))
        if "Output Report" in nodes:
            self._nr(
                r1,
                nodes["Output Report"],
                WorkflowNodeRun.COMPLETED,
                notify_out,
                {"data": {"report": {"title": "Pipeline Output Table"}}, "routes": ["end"]},
                timedelta(hours=2, minutes=2),
                timedelta(hours=2),
            )
        for e in edges:
            self._er(r1, e, True, timedelta(hours=2, minutes=4))

        # Run 2 — completed, 45 minutes ago
        r2 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"city": "Poznań", "pop": 540000}},
            output_data={"data": {"notified": True}, "routes": ["end"]},
            started_at=now - timedelta(minutes=47),
            completed_at=now - timedelta(minutes=45),
        )
        v2 = {"data": {"valid": True, "raw": {"city": "Poznań", "pop": 540000}}, "routes": ["ok"]}
        t2 = {"data": {"result": {"CITY": "POZNAŃ", "POP": "540000"}}, "routes": ["done"]}
        n2 = {
            "data": {
                "notified": True,
                "payload": {"CITY": "POZNAŃ", "POP": "540000"},
                "rows": [
                    {"field": "CITY", "value": "POZNAŃ"},
                    {"field": "POP", "value": "540000"},
                ],
            },
            "routes": ["end"],
        }
        self._nr(r2, nodes["Validate"],  WorkflowNodeRun.COMPLETED, r2.input_data, v2, timedelta(minutes=47), timedelta(minutes=46, seconds=30))
        self._nr(r2, nodes["Transform"], WorkflowNodeRun.COMPLETED, v2,            t2, timedelta(minutes=46, seconds=30), timedelta(minutes=46))
        self._nr(r2, nodes["Notify"],    WorkflowNodeRun.COMPLETED, t2,            n2, timedelta(minutes=46), timedelta(minutes=45))
        if "Output Report" in nodes:
            self._nr(
                r2,
                nodes["Output Report"],
                WorkflowNodeRun.COMPLETED,
                n2,
                {"data": {"report": {"title": "Pipeline Output Table"}}, "routes": ["end"]},
                timedelta(minutes=45, seconds=30),
                timedelta(minutes=45),
            )
        for e in edges:
            self._er(r2, e, True, timedelta(minutes=46))

        # Run 3 — failed at Validate, 10 minutes ago
        r3 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.FAILED,
            input_data={"data": {}},
            started_at=now - timedelta(minutes=10),
        )
        self._nr(r3, nodes["Validate"], WorkflowNodeRun.FAILED, r3.input_data, None,
                 timedelta(minutes=10), timedelta(minutes=9, seconds=50),
                 error="Kernel error: timeout after 5000ms")

        self.stdout.write(self.style.SUCCESS("Seeded 3 runs: Data Pipeline"))

    def _backfill_pipeline_report_runs(self, wf):
        notify = wf.nodes.filter(label="Notify").first()
        report_node = wf.nodes.filter(label="Output Report").first()
        if notify is None or report_node is None:
            return
        edge = wf.edges.filter(source=notify, target=report_node).first()
        created_count = 0
        notify_runs = WorkflowNodeRun.objects.filter(
            node=notify,
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("completed_at")
        for notify_run in notify_runs:
            report_run, created = self._ensure_seed_report_node_run(
                workflow_run=notify_run.workflow_run,
                node=report_node,
                input_data=notify_run.output_data,
                title="Pipeline Output Table",
                completed_at=notify_run.completed_at,
            )
            created_count += int(created)
            self._ensure_seed_report_edge_run(
                workflow_run=notify_run.workflow_run,
                edge=edge,
                activated_at=report_run.started_at or report_run.completed_at,
            )
        if created_count:
            self.stdout.write(self.style.SUCCESS(f"  + report node runs: Data Pipeline ({created_count})"))

    # --- Human Approval Gate ---

    def _seed_approval_runs(self, now):
        try:
            wf = Workflow.objects.get(name="Human Approval Gate")
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING("Runs already exist: Human Approval Gate"))
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = {(e.source.label, e.target.label): e for e in wf.edges.select_related("source", "target")}

        score_out_approved = {"data": {"score": 87.4, "item_id": 42}, "routes": ["review"]}
        score_out_rejected = {"data": {"score": 23.1, "item_id": 99}, "routes": ["review"]}

        # Run 1 — completed, approved, 3 hours ago
        r1 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"item_id": 42}},
            output_data={"data": {"decision": "accepted", "score": 87.4}, "routes": ["end"]},
            started_at=now - timedelta(hours=3, minutes=10),
            completed_at=now - timedelta(hours=3),
        )
        human_out_approved = {"data": {"comment": "Looks great, ship it."}, "routes": ["approved"]}
        accept_out = {"data": {"decision": "accepted", "score": 87.4}, "routes": ["end"]}
        self._nr(r1, nodes["Score"],  WorkflowNodeRun.COMPLETED, r1.input_data,       score_out_approved,  timedelta(hours=3, minutes=10), timedelta(hours=3, minutes=9))
        self._nr(r1, nodes["Review"], WorkflowNodeRun.COMPLETED, score_out_approved,  human_out_approved,  timedelta(hours=3, minutes=9),  timedelta(hours=3, minutes=2))
        self._nr(r1, nodes["Accept"], WorkflowNodeRun.COMPLETED, human_out_approved,  accept_out,          timedelta(hours=3, minutes=2),  timedelta(hours=3))
        self._er(r1, edges[("Score",  "Review")], True,  timedelta(hours=3, minutes=9))
        self._er(r1, edges[("Review", "Accept")], True,  timedelta(hours=3, minutes=2))
        self._er(r1, edges[("Review", "Reject")], False)

        # Run 2 — completed, rejected, 1 hour ago
        r2 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"item_id": 99}},
            output_data={"data": {"decision": "rejected", "score": 23.1}, "routes": ["end"]},
            started_at=now - timedelta(hours=1, minutes=8),
            completed_at=now - timedelta(hours=1),
        )
        human_out_rejected = {"data": {"comment": "Too risky at this score."}, "routes": ["rejected"]}
        reject_out = {"data": {"decision": "rejected", "score": 23.1}, "routes": ["end"]}
        self._nr(r2, nodes["Score"],  WorkflowNodeRun.COMPLETED, r2.input_data,      score_out_rejected,  timedelta(hours=1, minutes=8), timedelta(hours=1, minutes=7))
        self._nr(r2, nodes["Review"], WorkflowNodeRun.COMPLETED, score_out_rejected, human_out_rejected,  timedelta(hours=1, minutes=7), timedelta(hours=1, minutes=1))
        self._nr(r2, nodes["Reject"], WorkflowNodeRun.COMPLETED, human_out_rejected, reject_out,          timedelta(hours=1, minutes=1), timedelta(hours=1))
        self._er(r2, edges[("Score",  "Review")], True,  timedelta(hours=1, minutes=7))
        self._er(r2, edges[("Review", "Accept")], False)
        self._er(r2, edges[("Review", "Reject")], True,  timedelta(hours=1, minutes=1))

        # Run 3 — running, review node pending (human-task concept removed in spine redesign)
        r3 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.RUNNING,
            input_data={"data": {"item_id": 77}},
            started_at=now - timedelta(minutes=6),
        )
        score_out_pending = {"data": {"score": 61.9, "item_id": 77}, "routes": ["review"]}
        nr_score = self._nr(r3, nodes["Score"],  WorkflowNodeRun.COMPLETED, r3.input_data,      score_out_pending, timedelta(minutes=6),  timedelta(minutes=5, seconds=45))
        nr_human = self._nr(r3, nodes["Review"], WorkflowNodeRun.PENDING,   score_out_pending,  None,              timedelta(minutes=5, seconds=45))
        self._er(r3, edges[("Score", "Review")], True, timedelta(minutes=5, seconds=45))
        self.stdout.write(self.style.SUCCESS("Seeded 3 runs: Human Approval Gate"))

    # --- Parallel Enrichment ---

    def _seed_enrichment_runs(self, now):
        try:
            wf = Workflow.objects.get(name="Parallel Enrichment")
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING("Runs already exist: Parallel Enrichment"))
            self._backfill_enrichment_report_runs(wf)
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = {(e.source.label, e.target.label): e for e in wf.edges.select_related("source", "target")}

        fetch_out  = {"data": {"entity": "acme-corp"}, "routes": ["geo", "financial", "social"]}
        geo_out    = {"data": {"geo": {"country": "PL", "city": "Poznań"}}, "routes": ["merged"]}
        fin_out    = {"data": {"financial": {"credit_score": 720}}, "routes": ["merged"]}
        soc_out    = {"data": {"social": {"followers": 1024}}, "routes": ["merged"]}
        merge_data = {"geo": {"country": "PL", "city": "Poznań"}, "financial": {"credit_score": 720}, "social": {"followers": 1024}}
        join_out   = {"data": merge_data, "routes": ["merged"]}
        summary_out = {"data": {"summary": merge_data, "series": self._summary_series(merge_data), "complete": True}, "routes": ["end"]}

        # Run 1 — completed, 90 minutes ago
        r1 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"entity": "acme-corp"}},
            output_data=summary_out,
            started_at=now - timedelta(minutes=92),
            completed_at=now - timedelta(minutes=90),
        )
        self._nr(r1, nodes["Fetch"],     WorkflowNodeRun.COMPLETED, r1.input_data, fetch_out,   timedelta(minutes=92),   timedelta(minutes=91, seconds=50))
        self._nr(r1, nodes["Fan-out"],   WorkflowNodeRun.COMPLETED, fetch_out,     fetch_out,   timedelta(minutes=91, seconds=50), timedelta(minutes=91, seconds=45))
        self._nr(r1, nodes["Geo"],       WorkflowNodeRun.COMPLETED, fetch_out,     geo_out,     timedelta(minutes=91, seconds=45), timedelta(minutes=91, seconds=20))
        self._nr(r1, nodes["Financial"], WorkflowNodeRun.COMPLETED, fetch_out,     fin_out,     timedelta(minutes=91, seconds=45), timedelta(minutes=91, seconds=10))
        self._nr(r1, nodes["Social"],    WorkflowNodeRun.COMPLETED, fetch_out,     soc_out,     timedelta(minutes=91, seconds=45), timedelta(minutes=91))
        self._nr(r1, nodes["Merge"],     WorkflowNodeRun.COMPLETED, join_out,      join_out,    timedelta(minutes=91),   timedelta(minutes=90, seconds=50))
        self._nr(r1, nodes["Summarise"], WorkflowNodeRun.COMPLETED, join_out,      summary_out, timedelta(minutes=90, seconds=50), timedelta(minutes=90))
        if "Summary Chart" in nodes:
            self._nr(
                r1,
                nodes["Summary Chart"],
                WorkflowNodeRun.COMPLETED,
                summary_out,
                {"data": {"report": {"title": "Enrichment Sections Chart"}}, "routes": ["end"]},
                timedelta(minutes=89, seconds=50),
                timedelta(minutes=89, seconds=45),
            )
        enrichment_edges = [("Fetch","Fan-out"),("Fan-out","Geo"),("Fan-out","Financial"),("Fan-out","Social"),("Geo","Merge"),("Financial","Merge"),("Social","Merge"),("Merge","Summarise")]
        if "Summary Chart" in nodes and ("Summarise", "Summary Chart") in edges:
            enrichment_edges.append(("Summarise", "Summary Chart"))
        for lbl, target_lbl in enrichment_edges:
            edge = edges.get((lbl, target_lbl))
            if edge:
                self._er(r1, edge, True, timedelta(minutes=91, seconds=45))

        # Run 2 — failed at Financial branch, 30 minutes ago
        r2 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.FAILED,
            input_data={"data": {"entity": "broken-co"}},
            started_at=now - timedelta(minutes=32),
        )
        self._nr(r2, nodes["Fetch"],     WorkflowNodeRun.COMPLETED, r2.input_data, fetch_out, timedelta(minutes=32),   timedelta(minutes=31, seconds=50))
        self._nr(r2, nodes["Fan-out"],   WorkflowNodeRun.COMPLETED, fetch_out,     fetch_out, timedelta(minutes=31, seconds=50), timedelta(minutes=31, seconds=45))
        self._nr(r2, nodes["Geo"],       WorkflowNodeRun.COMPLETED, fetch_out,     geo_out,   timedelta(minutes=31, seconds=45), timedelta(minutes=31, seconds=20))
        self._nr(r2, nodes["Financial"], WorkflowNodeRun.FAILED,    fetch_out,     None,      timedelta(minutes=31, seconds=45), timedelta(minutes=31, seconds=30),
                 error="Kernel error: financial API returned 503")
        self._nr(r2, nodes["Social"],    WorkflowNodeRun.COMPLETED, fetch_out,     soc_out,   timedelta(minutes=31, seconds=45), timedelta(minutes=31))
        self._er(r2, edges[("Fetch",     "Fan-out")],   True,  timedelta(minutes=31, seconds=50))
        self._er(r2, edges[("Fan-out",   "Geo")],       True,  timedelta(minutes=31, seconds=45))
        self._er(r2, edges[("Fan-out",   "Financial")], True,  timedelta(minutes=31, seconds=45))
        self._er(r2, edges[("Fan-out",   "Social")],    True,  timedelta(minutes=31, seconds=45))

        # Run 3 — completed with only geo + social (financial skipped), 5 minutes ago
        geo_soc_fetch = {"data": {"entity": "mini-llc"}, "routes": ["geo", "social"]}
        merge_gs = {"geo": {"country": "DE", "city": "Berlin"}, "social": {"followers": 320}}
        join_gs  = {"data": merge_gs, "routes": ["merged"]}
        summary_gs = {"data": {"summary": merge_gs, "series": self._summary_series(merge_gs), "complete": True}, "routes": ["end"]}
        r3 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"entity": "mini-llc"}},
            output_data=summary_gs,
            started_at=now - timedelta(minutes=6),
            completed_at=now - timedelta(minutes=5),
        )
        geo_out_gs = {"data": {"geo": {"country": "DE", "city": "Berlin"}}, "routes": ["merged"]}
        soc_out_gs = {"data": {"social": {"followers": 320}}, "routes": ["merged"]}
        self._nr(r3, nodes["Fetch"],     WorkflowNodeRun.COMPLETED, r3.input_data, geo_soc_fetch, timedelta(minutes=6),   timedelta(minutes=5, seconds=55))
        self._nr(r3, nodes["Fan-out"],   WorkflowNodeRun.COMPLETED, geo_soc_fetch, geo_soc_fetch, timedelta(minutes=5, seconds=55), timedelta(minutes=5, seconds=50))
        self._nr(r3, nodes["Geo"],       WorkflowNodeRun.COMPLETED, geo_soc_fetch, geo_out_gs,    timedelta(minutes=5, seconds=50), timedelta(minutes=5, seconds=30))
        self._nr(r3, nodes["Social"],    WorkflowNodeRun.COMPLETED, geo_soc_fetch, soc_out_gs,    timedelta(minutes=5, seconds=50), timedelta(minutes=5, seconds=20))
        self._nr(r3, nodes["Merge"],     WorkflowNodeRun.COMPLETED, join_gs,       join_gs,        timedelta(minutes=5, seconds=20), timedelta(minutes=5, seconds=10))
        self._nr(r3, nodes["Summarise"], WorkflowNodeRun.COMPLETED, join_gs,       summary_gs,    timedelta(minutes=5, seconds=10), timedelta(minutes=5))
        if "Summary Chart" in nodes:
            self._nr(
                r3,
                nodes["Summary Chart"],
                WorkflowNodeRun.COMPLETED,
                summary_gs,
                {"data": {"report": {"title": "Enrichment Sections Chart"}}, "routes": ["end"]},
                timedelta(minutes=4, seconds=55),
                timedelta(minutes=4, seconds=50),
            )
        self._er(r3, edges[("Fetch",      "Fan-out")],   True,  timedelta(minutes=5, seconds=55))
        self._er(r3, edges[("Fan-out",    "Geo")],       True,  timedelta(minutes=5, seconds=50))
        self._er(r3, edges[("Fan-out",    "Financial")], False)
        self._er(r3, edges[("Fan-out",    "Social")],    True,  timedelta(minutes=5, seconds=50))
        self._er(r3, edges[("Geo",        "Merge")],     True,  timedelta(minutes=5, seconds=30))
        self._er(r3, edges[("Social",     "Merge")],     True,  timedelta(minutes=5, seconds=20))
        self._er(r3, edges[("Merge",      "Summarise")], True,  timedelta(minutes=5, seconds=10))
        report_edge = edges.get(("Summarise", "Summary Chart"))
        if "Summary Chart" in nodes and report_edge:
            self._er(r3, report_edge, True, timedelta(minutes=4, seconds=55))

        self.stdout.write(self.style.SUCCESS("Seeded 3 runs: Parallel Enrichment"))

    def _backfill_enrichment_report_runs(self, wf):
        summarise = wf.nodes.filter(label="Summarise").first()
        report_node = wf.nodes.filter(label="Summary Chart").first()
        if summarise is None or report_node is None:
            return
        edge = wf.edges.filter(source=summarise, target=report_node).first()
        created_count = 0
        summary_runs = WorkflowNodeRun.objects.filter(
            node=summarise,
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("completed_at")
        for summary_run in summary_runs:
            report_run, created = self._ensure_seed_report_node_run(
                workflow_run=summary_run.workflow_run,
                node=report_node,
                input_data=summary_run.output_data,
                title="Enrichment Sections Chart",
                completed_at=summary_run.completed_at,
            )
            created_count += int(created)
            self._ensure_seed_report_edge_run(
                workflow_run=summary_run.workflow_run,
                edge=edge,
                activated_at=report_run.started_at or report_run.completed_at,
            )
        if created_count:
            self.stdout.write(self.style.SUCCESS(f"  + report node runs: Parallel Enrichment ({created_count})"))

    # --- Fake backend chart flows ---

    def _seed_metrics_trend_runs(self, now):
        self._seed_single_report_example_runs(
            now=now,
            workflow_name="Metrics Trend Report",
            source_label="Build Trend Series",
            report_label="Trend Line Report",
            report_title="Weekly Metrics Trend",
            input_payload={"metric": "active_items"},
            series=[
                {"label": "Mon", "value": 42},
                {"label": "Tue", "value": 51},
                {"label": "Wed", "value": 48},
                {"label": "Thu", "value": 64},
                {"label": "Fri", "value": 73},
                {"label": "Sat", "value": 69},
                {"label": "Sun", "value": 81},
            ],
            start_ago=timedelta(minutes=24),
            finish_ago=timedelta(minutes=22),
        )

    def _seed_channel_mix_runs(self, now):
        self._seed_single_report_example_runs(
            now=now,
            workflow_name="Channel Mix Report",
            source_label="Build Channel Mix",
            report_label="Channel Mix Report",
            report_title="Acquisition Channel Mix",
            input_payload={"window": "last_30_days"},
            series=[
                {"label": "Organic", "value": 44},
                {"label": "Social", "value": 27},
                {"label": "Referral", "value": 16},
                {"label": "Paid", "value": 13},
            ],
            start_ago=timedelta(minutes=19),
            finish_ago=timedelta(minutes=17),
        )

    def _seed_file_client_runs(self, now):
        try:
            wf = Workflow.objects.get(name="File Client Demo")
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING("Runs already exist: File Client Demo"))
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = list(wf.edges.select_related("source", "target").all())
        if "Write File" not in nodes:
            return

        payload = {"demo": True, "value": 42}
        rows = [{"field": "demo", "value": "True"}, {"field": "value", "value": "42"}]
        write_out = {"data": {"written": True, "path": "workflow-demo/input.json", "payload": payload}, "routes": ["read"]}
        read_out  = {"data": {"read": payload, "log_path": "workflow-demo/run.log"}, "routes": ["summarise"]}
        summarise_out = {"data": {"rows": rows, "output_path": "workflow-demo/output.json"}, "routes": ["end"]}

        r1 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.COMPLETED,
            input_data={"data": {"payload": payload}},
            output_data=summarise_out,
            started_at=now - timedelta(minutes=8),
            completed_at=now - timedelta(minutes=7),
        )
        self._nr(r1, nodes["Write File"],  WorkflowNodeRun.COMPLETED, r1.input_data, write_out,     timedelta(minutes=8),               timedelta(minutes=7, seconds=50))
        self._nr(r1, nodes["Read File"],   WorkflowNodeRun.COMPLETED, write_out,     read_out,      timedelta(minutes=7, seconds=50),   timedelta(minutes=7, seconds=40))
        self._nr(r1, nodes["Summarise"],   WorkflowNodeRun.COMPLETED, read_out,      summarise_out, timedelta(minutes=7, seconds=40),   timedelta(minutes=7, seconds=30))
        if "File Output Report" in nodes:
            self._nr(
                r1,
                nodes["File Output Report"],
                WorkflowNodeRun.COMPLETED,
                summarise_out,
                {"data": {"report": {"title": "File Client Demo Output"}}, "routes": ["end"]},
                timedelta(minutes=7, seconds=20),
                timedelta(minutes=7),
            )
        for e in edges:
            self._er(r1, e, True, timedelta(minutes=7, seconds=50))

        self.stdout.write(self.style.SUCCESS("Seeded 1 run: File Client Demo"))

    def _seed_weather_runs(self, now):
        try:
            wf = Workflow.objects.get(name="Weather Forecast Report")
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING("Runs already exist: Weather Forecast Report"))
            self._backfill_weather_report_runs(wf)
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = {(e.source.label, e.target.label): e for e in wf.edges.select_related("source", "target")}
        if (
            "Weather Gate" not in nodes
            or "Build Forecast Request" not in nodes
            or "Fetch Forecast" not in nodes
            or "Build Weather Series" not in nodes
        ):
            return

        forecast_payload = self._weather_forecast_payload()
        weather_data = self._weather_report_data(forecast_payload)
        parameters = {
            "city": "Poznan",
            "latitude": 52.4069,
            "longitude": 16.9252,
            "forecast_days": 7,
        }
        request_output = {
            "data": {
                "city": "Poznan",
                "params": {
                    "latitude": 52.4069,
                    "longitude": 16.9252,
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
                    "forecast_days": 7,
                    "timezone": "Europe/Warsaw",
                },
            },
            "routes": ["fetch"],
        }
        fetch_output = {
            "data": {
                "status_code": 200,
                "headers": {"Content-Type": "application/json"},
                "json": forecast_payload,
                "truncated": False,
            },
            "routes": [],
        }
        series_output = {"data": weather_data, "routes": ["report"]}
        run = WorkflowRun.objects.create(
            workflow=wf,
            status=WorkflowRun.COMPLETED,
            output_data=series_output,
            started_at=now - timedelta(minutes=14),
            completed_at=now - timedelta(minutes=12),
        )
        lambda_payload = {
            "workflow_id": str(wf.id),
            "run_id": str(run.id),
            "trigger_node_id": str(nodes["Weather Gate"].id),
            "inputs": parameters,
        }
        run.input_data = {
            "trigger_node_id": nodes["Weather Gate"].id,
            "parameters": parameters,
            "files": {},
            "inputs": parameters,
            "lambda_payload": lambda_payload,
        }
        run.save(update_fields=["input_data"])
        trigger_output = {"data": lambda_payload, "routes": ["start"]}
        self._nr(
            run,
            nodes["Weather Gate"],
            WorkflowNodeRun.COMPLETED,
            run.input_data,
            trigger_output,
            timedelta(minutes=14, seconds=30),
            timedelta(minutes=14, seconds=20),
        )
        self._nr(
            run,
            nodes["Build Forecast Request"],
            WorkflowNodeRun.COMPLETED,
            trigger_output,
            request_output,
            timedelta(minutes=14, seconds=20),
            timedelta(minutes=14),
        )
        self._nr(
            run,
            nodes["Fetch Forecast"],
            WorkflowNodeRun.COMPLETED,
            request_output,
            fetch_output,
            timedelta(minutes=14),
            timedelta(minutes=13, seconds=45),
        )
        self._nr(
            run,
            nodes["Build Weather Series"],
            WorkflowNodeRun.COMPLETED,
            fetch_output,
            series_output,
            timedelta(minutes=13, seconds=45),
            timedelta(minutes=13, seconds=20),
        )
        for report_label, title, start_offset in (
            ("Temperature Report", "Poznan Temperature Forecast", timedelta(minutes=13, seconds=10)),
            ("Precipitation Report", "Poznan Precipitation Forecast", timedelta(minutes=13)),
        ):
            if report_label in nodes:
                self._nr(
                    run,
                    nodes[report_label],
                    WorkflowNodeRun.COMPLETED,
                    series_output,
                    {"data": {"report": {"title": title}}, "routes": ["end"]},
                    start_offset,
                    timedelta(minutes=12),
        )
        for edge_key, activated_at in (
            (("Weather Gate", "Build Forecast Request"), timedelta(minutes=14, seconds=20)),
            (("Build Forecast Request", "Fetch Forecast"), timedelta(minutes=14)),
            (("Fetch Forecast", "Build Weather Series"), timedelta(minutes=13, seconds=45)),
            (("Build Weather Series", "Temperature Report"), timedelta(minutes=13, seconds=10)),
            (("Build Weather Series", "Precipitation Report"), timedelta(minutes=13)),
        ):
            edge = edges.get(edge_key)
            if edge:
                self._er(run, edge, True, activated_at)
        self.stdout.write(self.style.SUCCESS("Seeded 1 run: Weather Forecast Report"))

    def _backfill_weather_report_runs(self, wf):
        source = wf.nodes.filter(label="Build Weather Series").first()
        if source is None:
            return
        created_count = 0
        for source_run in WorkflowNodeRun.objects.filter(
            node=source,
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("completed_at"):
            for report_label, title in (
                ("Temperature Report", "Poznan Temperature Forecast"),
                ("Precipitation Report", "Poznan Precipitation Forecast"),
            ):
                report_node = wf.nodes.filter(label=report_label).first()
                if report_node is None:
                    continue
                edge = wf.edges.filter(source=source, target=report_node).first()
                report_run, created = self._ensure_seed_report_node_run(
                    workflow_run=source_run.workflow_run,
                    node=report_node,
                    input_data=source_run.output_data,
                    title=title,
                    completed_at=source_run.completed_at,
                )
                created_count += int(created)
                self._ensure_seed_report_edge_run(
                    workflow_run=source_run.workflow_run,
                    edge=edge,
                    activated_at=report_run.started_at or report_run.completed_at,
                )
        if created_count:
            self.stdout.write(
                self.style.SUCCESS(f"  + report node runs: Weather Forecast Report ({created_count})")
            )

    def _weather_forecast_payload(self):
        return {
            "latitude": 52.4,
            "longitude": 16.9,
            "generationtime_ms": 0.14,
            "timezone": "Europe/Warsaw",
            "daily_units": {
                "time": "iso8601",
                "temperature_2m_max": "degC",
                "temperature_2m_min": "degC",
                "precipitation_sum": "mm",
            },
            "daily": {
                "time": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
                "temperature_2m_max": [19.4, 21.1, 20.3, 18.7, 22.6, 24.1, 23.3],
                "temperature_2m_min": [9.2, 10.4, 11.1, 8.9, 12.0, 13.2, 12.8],
                "precipitation_sum": [0.0, 0.4, 2.8, 6.1, 1.3, 0.0, 0.2],
            },
        }

    def _weather_report_data(self, payload):
        daily = payload.get("daily", {})
        units = payload.get("daily_units", {})
        dates = daily.get("time", [])
        max_t = daily.get("temperature_2m_max", [])
        min_t = daily.get("temperature_2m_min", [])
        precip = daily.get("precipitation_sum", [])
        return {
            "location": "Poznan",
            "source": "Open-Meteo",
            "temperature_unit": units.get("temperature_2m_max", "degC"),
            "precipitation_unit": units.get("precipitation_sum", "mm"),
            "temperature_series": [
                {
                    "label": label,
                    "value": max_t[index],
                    "min": min_t[index] if index < len(min_t) else None,
                }
                for index, label in enumerate(dates)
                if index < len(max_t)
            ],
            "precipitation_series": [
                {"label": label, "value": precip[index]}
                for index, label in enumerate(dates)
                if index < len(precip)
            ],
        }

    def _seed_single_report_example_runs(
        self,
        *,
        now,
        workflow_name,
        source_label,
        report_label,
        report_title,
        input_payload,
        series,
        start_ago,
        finish_ago,
    ):
        try:
            wf = Workflow.objects.get(name=workflow_name)
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING(f"Runs already exist: {workflow_name}"))
            self._backfill_single_report_example_runs(
                wf=wf,
                source_label=source_label,
                report_label=report_label,
                report_title=report_title,
            )
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = {(e.source.label, e.target.label): e for e in wf.edges.select_related("source", "target")}
        if source_label not in nodes:
            return

        output_data = {"data": {"series": series}, "routes": ["report"]}
        r1 = WorkflowRun.objects.create(
            workflow=wf,
            status=WorkflowRun.COMPLETED,
            input_data={"data": input_payload},
            output_data={"data": {"series": series}, "routes": ["end"]},
            started_at=now - start_ago,
            completed_at=now - finish_ago,
        )
        self._nr(
            r1,
            nodes[source_label],
            WorkflowNodeRun.COMPLETED,
            r1.input_data,
            output_data,
            start_ago,
            finish_ago + timedelta(seconds=20),
        )
        if report_label in nodes:
            self._nr(
                r1,
                nodes[report_label],
                WorkflowNodeRun.COMPLETED,
                output_data,
                {"data": {"report": {"title": report_title}}, "routes": ["end"]},
                finish_ago + timedelta(seconds=10),
                finish_ago,
            )
            edge = edges.get((source_label, report_label))
            if edge:
                self._er(r1, edge, True, finish_ago + timedelta(seconds=10))
        self.stdout.write(self.style.SUCCESS(f"Seeded 1 run: {workflow_name}"))

    def _backfill_single_report_example_runs(self, *, wf, source_label, report_label, report_title):
        source = wf.nodes.filter(label=source_label).first()
        report_node = wf.nodes.filter(label=report_label).first()
        if source is None or report_node is None:
            return
        edge = wf.edges.filter(source=source, target=report_node).first()
        created_count = 0
        source_runs = WorkflowNodeRun.objects.filter(
            node=source,
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("workflow_run").order_by("completed_at")
        for source_run in source_runs:
            report_run, created = self._ensure_seed_report_node_run(
                workflow_run=source_run.workflow_run,
                node=report_node,
                input_data=source_run.output_data,
                title=report_title,
                completed_at=source_run.completed_at,
            )
            created_count += int(created)
            self._ensure_seed_report_edge_run(
                workflow_run=source_run.workflow_run,
                edge=edge,
                activated_at=report_run.started_at or report_run.completed_at,
            )
        if created_count:
            self.stdout.write(self.style.SUCCESS(f"  + report node runs: {wf.name} ({created_count})"))
