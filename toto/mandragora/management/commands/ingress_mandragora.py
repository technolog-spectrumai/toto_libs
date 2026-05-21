from pathlib import Path

from django.conf import settings
from toto.ingress import IngressCommand

from django.utils import timezone
from datetime import timedelta

from toto.api.models import Connector as ApiConnector
from toto.core.connectors import list_connector_types, validate_connector_type
from toto.mandragora.models import (
    Cell, ComputeKernel, KernelDependency, Notebook,
)
from toto.workflows.models import (
    LambdaFunction, Workflow, WorkflowConnector, WorkflowEdge, WorkflowNode,
    WorkflowRun, WorkflowNodeRun, WorkflowEdgeRun, HumanTask,
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
        else:
            self.stdout.write(self.style.WARNING(f"Notebook already exists: {notebook.title}"))

        self._seed_connectors()
        self._seed_workflows()
        self._seed_runs()

    # ------------------------------------------------------------------
    #  Connector seeds
    # ------------------------------------------------------------------

    def _seed_connectors(self):
        self._seed_connector_sample_file()
        api_connector = self._seed_api_connector()

        seed_configs = {
            WorkflowConnector.FILE_READ: {
                "path": "ingress/sample-input.json",
                "encoding": "utf-8",
            },
            WorkflowConnector.FILE_WRITE: {
                "path": "ingress/sample-output.json",
                "json_field": "data",
                "mode": "overwrite",
            },
            WorkflowConnector.API_REQUEST: {
                "api_connector_slug": api_connector.slug,
                "endpoint": "anything/mandragora-ingress",
                "method": "GET",
                "params": {"source": "mandragora-ingress"},
                "timeout_seconds": 10,
            },
            WorkflowConnector.PEOPLE_READ: {
                "action": "list",
                "limit": 10,
            },
            WorkflowConnector.SOCIALHUB_READ: {
                "resource": "community",
                "action": "list",
                "limit": 10,
            },
            WorkflowConnector.LOCATIONS_READ: {
                "resource": "address",
                "action": "list",
                "limit": 10,
            },
            WorkflowConnector.EVENTS_READ: {
                "resource": "scheduled_event",
                "action": "list",
                "limit": 10,
                "public_only": True,
            },
        }

        for connector_info in list_connector_types():
            connector_type = connector_info["connector_type"]
            config = seed_configs.get(connector_type)
            if config is None:
                self.stdout.write(
                    self.style.WARNING(f"No ingress seed config for connector type: {connector_type}")
                )
                continue

            errors = validate_connector_type(connector_type, config)
            if errors:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping connector seed {connector_type}: {'; '.join(errors)}"
                    )
                )
                continue

            name = f"Demo {connector_info['label']}"
            _, created = WorkflowConnector.objects.update_or_create(
                name=name,
                defaults={
                    "connector_type": connector_type,
                    "config": config,
                },
            )
            if created:
                self.stdout.write(self.style.SUCCESS(f"  + connector: {name}"))

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

    # ------------------------------------------------------------------
    #  Workflow seeds
    # ------------------------------------------------------------------

    def _seed_workflows(self):
        self._seed_linear_workflow()
        self._seed_approval_workflow()
        self._seed_fanout_workflow()

    def _upsert_lambda(self, name: str, src: str) -> LambdaFunction:
        fn, created = LambdaFunction.objects.update_or_create(
            function_name=name,
            defaults={"content": src},
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"  + lambda: {name}"))
        return fn

    def _seed_linear_workflow(self):
        """validate → transform → notify (3 lambda nodes in sequence)."""
        wf, created = Workflow.objects.get_or_create(
            name="Data Pipeline",
            defaults={"description": "Validate, transform and notify — a simple 3-step linear pipeline."},
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: Data Pipeline"))
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
print(json.dumps({"data": {"notified": True, "payload": result}, "route": "end"}))
""")

        n1 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Validate",  lambda_function=fn_validate,  position_x=0,   position_y=0)
        n2 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Transform", lambda_function=fn_transform, position_x=0,   position_y=120)
        n3 = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Notify",    lambda_function=fn_notify,    position_x=0,   position_y=240)
        WorkflowEdge.objects.create(workflow=wf, source=n1, target=n2)
        WorkflowEdge.objects.create(workflow=wf, source=n2, target=n3)
        self.stdout.write(self.style.SUCCESS("Created workflow: Data Pipeline"))

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
        n_human  = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.HUMAN,  label="Review",  config=human_config,        position_x=0,    position_y=120)
        n_accept = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Accept",  lambda_function=fn_accept, position_x=-150, position_y=240)
        n_reject = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Reject",  lambda_function=fn_reject, position_x=150,  position_y=240)
        WorkflowEdge.objects.create(workflow=wf, source=n_score,  target=n_human)
        WorkflowEdge.objects.create(workflow=wf, source=n_human,  target=n_accept, branch_key="approved")
        WorkflowEdge.objects.create(workflow=wf, source=n_human,  target=n_reject, branch_key="rejected")
        self.stdout.write(self.style.SUCCESS("Created workflow: Human Approval Gate"))

    def _seed_fanout_workflow(self):
        """fetch → split into 3 parallel enrichment lambdas → join → summarise."""
        wf, created = Workflow.objects.get_or_create(
            name="Parallel Enrichment",
            defaults={"description": "Fan out to three enrichment lambdas in parallel, then merge and summarise."},
        )
        if not created:
            self.stdout.write(self.style.WARNING("Workflow already exists: Parallel Enrichment"))
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
print(json.dumps({"data": {"summary": d, "complete": True}, "route": "end"}))
""")

        n_fetch     = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Fetch",      lambda_function=fn_fetch,     position_x=0,    position_y=0)
        n_split     = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.SPLIT,  label="Fan-out",                                  position_x=0,    position_y=100)
        n_geo       = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Geo",        lambda_function=fn_geo,       position_x=-200, position_y=200)
        n_fin       = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Financial",  lambda_function=fn_fin,       position_x=0,    position_y=200)
        n_soc       = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Social",     lambda_function=fn_soc,       position_x=200,  position_y=200)
        n_join      = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.JOIN,   label="Merge",                                    position_x=0,    position_y=300)
        n_summarise = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="Summarise",  lambda_function=fn_summarise, position_x=0,    position_y=400)
        WorkflowEdge.objects.create(workflow=wf, source=n_fetch,     target=n_split)
        WorkflowEdge.objects.create(workflow=wf, source=n_split,     target=n_geo,       branch_key="geo")
        WorkflowEdge.objects.create(workflow=wf, source=n_split,     target=n_fin,       branch_key="financial")
        WorkflowEdge.objects.create(workflow=wf, source=n_split,     target=n_soc,       branch_key="social")
        WorkflowEdge.objects.create(workflow=wf, source=n_geo,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_fin,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_soc,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_join,      target=n_summarise)
        self.stdout.write(self.style.SUCCESS("Created workflow: Parallel Enrichment"))

    # ------------------------------------------------------------------
    #  Fake run seeds
    # ------------------------------------------------------------------

    def _seed_runs(self):
        now = timezone.now()

        self._seed_pipeline_runs(now)
        self._seed_approval_runs(now)
        self._seed_enrichment_runs(now)

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
        notify_out = {"data": {"notified": True, "payload": {"NAME": "ALICE", "SCORE": "98"}}, "routes": ["end"]}
        self._nr(r1, nodes["Validate"],  WorkflowNodeRun.COMPLETED, r1.input_data,  validate_out,  timedelta(hours=2, minutes=5), timedelta(hours=2, minutes=4))
        self._nr(r1, nodes["Transform"], WorkflowNodeRun.COMPLETED, validate_out,   transform_out, timedelta(hours=2, minutes=4), timedelta(hours=2, minutes=3))
        self._nr(r1, nodes["Notify"],    WorkflowNodeRun.COMPLETED, transform_out,  notify_out,    timedelta(hours=2, minutes=3), timedelta(hours=2))
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
        n2 = {"data": {"notified": True, "payload": {"CITY": "POZNAŃ", "POP": "540000"}}, "routes": ["end"]}
        self._nr(r2, nodes["Validate"],  WorkflowNodeRun.COMPLETED, r2.input_data, v2, timedelta(minutes=47), timedelta(minutes=46, seconds=30))
        self._nr(r2, nodes["Transform"], WorkflowNodeRun.COMPLETED, v2,            t2, timedelta(minutes=46, seconds=30), timedelta(minutes=46))
        self._nr(r2, nodes["Notify"],    WorkflowNodeRun.COMPLETED, t2,            n2, timedelta(minutes=46), timedelta(minutes=45))
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

        # Run 3 — paused, awaiting human, 5 minutes ago
        r3 = WorkflowRun.objects.create(
            workflow=wf, status=WorkflowRun.PAUSED,
            input_data={"data": {"item_id": 77}},
            started_at=now - timedelta(minutes=6),
        )
        score_out_pending = {"data": {"score": 61.9, "item_id": 77}, "routes": ["review"]}
        nr_score = self._nr(r3, nodes["Score"],  WorkflowNodeRun.COMPLETED, r3.input_data,      score_out_pending, timedelta(minutes=6),  timedelta(minutes=5, seconds=45))
        nr_human = self._nr(r3, nodes["Review"], WorkflowNodeRun.WAITING,   score_out_pending,  None,              timedelta(minutes=5, seconds=45))
        self._er(r3, edges[("Score", "Review")], True, timedelta(minutes=5, seconds=45))
        HumanTask.objects.create(
            node_run=nr_human,
            status=HumanTask.PENDING,
            form_schema=nodes["Review"].config.get("schema", {}),
        )

        self.stdout.write(self.style.SUCCESS("Seeded 3 runs: Human Approval Gate"))

    # --- Parallel Enrichment ---

    def _seed_enrichment_runs(self, now):
        try:
            wf = Workflow.objects.get(name="Parallel Enrichment")
        except Workflow.DoesNotExist:
            return

        if WorkflowRun.objects.filter(workflow=wf).exists():
            self.stdout.write(self.style.WARNING("Runs already exist: Parallel Enrichment"))
            return

        nodes = {n.label: n for n in wf.nodes.all()}
        edges = {(e.source.label, e.target.label): e for e in wf.edges.select_related("source", "target")}

        fetch_out  = {"data": {"entity": "acme-corp"}, "routes": ["geo", "financial", "social"]}
        geo_out    = {"data": {"geo": {"country": "PL", "city": "Poznań"}}, "routes": ["merged"]}
        fin_out    = {"data": {"financial": {"credit_score": 720}}, "routes": ["merged"]}
        soc_out    = {"data": {"social": {"followers": 1024}}, "routes": ["merged"]}
        merge_data = {"geo": {"country": "PL", "city": "Poznań"}, "financial": {"credit_score": 720}, "social": {"followers": 1024}}
        join_out   = {"data": merge_data, "routes": ["merged"]}
        summary_out = {"data": {"summary": merge_data, "complete": True}, "routes": ["end"]}

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
        for lbl, target_lbl in [("Fetch","Fan-out"),("Fan-out","Geo"),("Fan-out","Financial"),("Fan-out","Social"),("Geo","Merge"),("Financial","Merge"),("Social","Merge"),("Merge","Summarise")]:
            self._er(r1, edges[(lbl, target_lbl)], True, timedelta(minutes=91, seconds=45))

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
        summary_gs = {"data": {"summary": merge_gs, "complete": True}, "routes": ["end"]}
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
        self._er(r3, edges[("Fetch",      "Fan-out")],   True,  timedelta(minutes=5, seconds=55))
        self._er(r3, edges[("Fan-out",    "Geo")],       True,  timedelta(minutes=5, seconds=50))
        self._er(r3, edges[("Fan-out",    "Financial")], False)
        self._er(r3, edges[("Fan-out",    "Social")],    True,  timedelta(minutes=5, seconds=50))
        self._er(r3, edges[("Geo",        "Merge")],     True,  timedelta(minutes=5, seconds=30))
        self._er(r3, edges[("Social",     "Merge")],     True,  timedelta(minutes=5, seconds=20))
        self._er(r3, edges[("Merge",      "Summarise")], True,  timedelta(minutes=5, seconds=10))

        self.stdout.write(self.style.SUCCESS("Seeded 3 runs: Parallel Enrichment"))
