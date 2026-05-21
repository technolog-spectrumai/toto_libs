r"""
management command: example_workflow

Creates and runs a fully wired example DAG workflow that demonstrates every
node type: lambda → split → [branch_a, branch_b] → join → human → lambda.

Usage
-----
  python manage.py example_workflow                  # dry-run (build + validate only)
  python manage.py example_workflow --run            # build and execute
  python manage.py example_workflow --run --mock     # execute with a mock kernel (no real server required)
  python manage.py example_workflow --destroy        # delete the example workflow + all runs

The graph
---------
  [lambda: prepare]
        |
    [split]
     /       \
[lambda:     [lambda:
 branch_a]    branch_b]
     \       /
     [join]
        |
  [human: review]
        |
  [lambda: finalize]

Lambda code
-----------
All lambda functions print a JSON object to stdout. The kernel executes
each function with `_input` pre-bound to the incoming data dict.

Human node
----------
The review node expects the operator to POST to the submit endpoint.
The command prints the task ID and curl snippet after execution.

Kernel mock
-----------
With --mock the command monkey-patches the executor to use a fake kernel
that immediately returns hardcoded outputs.
"""

import json
import sys

from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings

from toto.workflows.models import (
    LambdaFunction,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from toto.workflows.services.executor import WorkflowExecutor
from toto.workflows.services.validator import ValidationError, WorkflowValidator

WORKFLOW_NAME = "Example Workflow (demo)"

# ---------------------------------------------------------------------------
#  Lambda source snippets
# ---------------------------------------------------------------------------

_PREPARE_SRC = """\
import json
# _input is pre-bound by the executor
print(json.dumps({
    "data": {"prepared": True, "seed": _input.get("seed", 42)},
    "routes": ["path_a", "path_b"],
}))
"""

_BRANCH_A_SRC = """\
import json
print(json.dumps({
    "data": {"branch": "a", "value": _input.get("data", {}).get("seed", 0) * 2},
    "route": "merged",
}))
"""

_BRANCH_B_SRC = """\
import json
print(json.dumps({
    "data": {"branch": "b", "value": _input.get("data", {}).get("seed", 0) * 3},
    "route": "merged",
}))
"""

_FINALIZE_SRC = """\
import json
print(json.dumps({
    "data": {"done": True, "route_taken": _input.get("routes", [])},
    "route": "end",
}))
"""

# ---------------------------------------------------------------------------
#  Human node config
# ---------------------------------------------------------------------------

HUMAN_CONFIG = {
    "schema": {
        "type": "object",
        "properties": {
            "approved": {"type": "boolean", "title": "Approve?"},
            "comment":  {"type": "string",  "title": "Comment"},
        },
        "required": ["approved"],
    },
    "output_mapping": {
        "route": {
            "field": "approved",
            "map": {"True": "approved", "False": "rejected"},
        },
        "data": {
            "comment": {"field": "comment"},
        },
    },
}


# ---------------------------------------------------------------------------
#  Mock kernel client (for --mock mode)
# ---------------------------------------------------------------------------

_MOCK_OUTPUTS = {}  # fn_id -> stdout payload, populated during build


def _build_mock_client():
    import json as _json

    class MockKernelClient:
        def __init__(self):
            pass

        def execute(self, fn_id, code):
            payload = _MOCK_OUTPUTS.get(fn_id)
            if payload is None:
                return {"stdout": '{"data": {}, "routes": []}'}
            return {"stdout": _json.dumps(payload)}

    return MockKernelClient


# ---------------------------------------------------------------------------
#  Command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Create and optionally run an example DAG workflow."

    def add_arguments(self, parser):
        parser.add_argument("--run",     action="store_true", help="Execute the workflow after building it.")
        parser.add_argument("--mock",    action="store_true", help="Use a mock kernel (no real server required).")
        parser.add_argument("--destroy", action="store_true", help="Delete the example workflow and all its runs.")

    def handle(self, *args, **options):
        if options["destroy"]:
            self._destroy()
            return

        workflow = self._build()
        self._validate(workflow)

        if options["run"]:
            self._execute(workflow, mock=options["mock"])
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nWorkflow '{workflow.name}' (id={workflow.id}) built and validated.\n"
                f"Run with:  python manage.py example_workflow --run --mock\n"
            ))

    # ------------------------------------------------------------------

    def _destroy(self):
        deleted, _ = Workflow.objects.filter(name=WORKFLOW_NAME).delete()
        if deleted:
            self.stdout.write(self.style.WARNING(f"Deleted example workflow and {deleted} related objects."))
        else:
            self.stdout.write("No example workflow found.")

    def _build(self) -> Workflow:
        wf, created = Workflow.objects.get_or_create(
            name=WORKFLOW_NAME,
            defaults={"description": "Auto-generated demo workflow covering all node types."},
        )

        if not created:
            self.stdout.write(f"Re-using existing workflow id={wf.id}")
            return wf

        self.stdout.write(f"Building workflow id={wf.id} ...")

        # Lambda functions
        fn_prepare  = self._upsert_lambda("demo_prepare",  _PREPARE_SRC)
        fn_branch_a = self._upsert_lambda("demo_branch_a", _BRANCH_A_SRC)
        fn_branch_b = self._upsert_lambda("demo_branch_b", _BRANCH_B_SRC)
        fn_finalize = self._upsert_lambda("demo_finalize", _FINALIZE_SRC)

        # Nodes
        n_prepare  = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA,  label="prepare",  lambda_function=fn_prepare,  position_x=0,    position_y=0)
        n_split    = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.SPLIT,   label="split",                                position_x=0,    position_y=100)
        n_a        = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA,  label="branch_a", lambda_function=fn_branch_a, position_x=-150, position_y=200)
        n_b        = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA,  label="branch_b", lambda_function=fn_branch_b, position_x=150,  position_y=200)
        n_join     = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.JOIN,    label="join",                                position_x=0,    position_y=300)
        n_human    = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.HUMAN,   label="review",   config=HUMAN_CONFIG,         position_x=0,    position_y=400)
        n_finalize = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA,  label="finalize", lambda_function=fn_finalize, position_x=0,    position_y=500)

        # Edges
        WorkflowEdge.objects.create(workflow=wf, source=n_prepare,  target=n_split)
        WorkflowEdge.objects.create(workflow=wf, source=n_split,    target=n_a,        branch_key="path_a")
        WorkflowEdge.objects.create(workflow=wf, source=n_split,    target=n_b,        branch_key="path_b")
        WorkflowEdge.objects.create(workflow=wf, source=n_a,        target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_b,        target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_join,     target=n_human)
        WorkflowEdge.objects.create(workflow=wf, source=n_human,    target=n_finalize, branch_key="approved")

        # Mock outputs keyed by lambda fn id
        _MOCK_OUTPUTS[fn_prepare.id]  = {"data": {"prepared": True, "seed": 42}, "routes": ["path_a", "path_b"]}
        _MOCK_OUTPUTS[fn_branch_a.id] = {"data": {"branch": "a", "value": 84}, "route": "merged"}
        _MOCK_OUTPUTS[fn_branch_b.id] = {"data": {"branch": "b", "value": 126}, "route": "merged"}
        _MOCK_OUTPUTS[fn_finalize.id] = {"data": {"done": True}, "route": "end"}

        self.stdout.write(self.style.SUCCESS("  ✓ 7 nodes, 7 edges created."))
        return wf

    def _upsert_lambda(self, name: str, src: str) -> LambdaFunction:
        fn, _ = LambdaFunction.objects.update_or_create(
            function_name=name,
            defaults={"content": src},
        )
        return fn

    def _validate(self, wf: Workflow):
        try:
            WorkflowValidator().validate(wf)
            self.stdout.write(self.style.SUCCESS("  ✓ Validation passed."))
        except ValidationError as exc:
            self.stderr.write(self.style.ERROR("Validation failed:"))
            for e in exc.errors:
                self.stderr.write(f"  • {e}")
            raise CommandError("Fix validation errors before running.")

    def _execute(self, wf: Workflow, mock: bool = False):
        run = WorkflowRun.objects.create(workflow=wf, input_data={"seed": 7})
        self.stdout.write(f"\nStarting run id={run.id} ...")

        executor = WorkflowExecutor()
        if mock:
            MockClient = _build_mock_client()
            with override_settings(WORKFLOW_KERNEL_CLIENT=MockClient):
                executor.start(run)
        else:
            executor.start(run)

        run.refresh_from_db()
        self.stdout.write(f"\nRun status: {self.style.SUCCESS(run.status.upper())}")

        # Print node run summary
        self.stdout.write("\nNode runs:")
        for nr in run.node_runs.select_related("node").order_by("id"):
            status_str = self.style.SUCCESS(nr.status) if nr.status == "completed" else self.style.WARNING(nr.status)
            self.stdout.write(f"  [{status_str}] {nr.node.label} ({nr.node.node_type})")

        # Human task hint
        from ...models import HumanTask
        tasks = HumanTask.objects.filter(node_run__workflow_run=run, status=HumanTask.PENDING)
        if tasks.exists():
            task = tasks.first()
            self.stdout.write(self.style.WARNING(
                f"\nWorkflow is PAUSED awaiting human input (task id={task.id}).\n"
                f"Submit via API:\n"
                f'  curl -X POST http://localhost:8000/mandragora/api/human-tasks/{task.id}/submit/ \\\n'
                f'       -H "Content-Type: application/json" \\\n'
                f'       -d \'{{"submitted_data": {{"approved": true, "comment": "LGTM"}}}}\'\n'
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\nWorkflow completed without human input (check mock outputs)."))
