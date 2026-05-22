r"""
management command: example_workflow

Creates and runs an example DAG workflow: lambda → split → [branch_a, branch_b] → join → lambda.

Usage
-----
  python manage.py example_workflow           # dry-run (build + validate only)
  python manage.py example_workflow --run     # build and execute synchronously
  python manage.py example_workflow --destroy # delete the example workflow + all runs
"""

from django.core.management.base import BaseCommand, CommandError

from ...models import (
    LambdaFunction,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowRun,
)
from ...services.executor import WorkflowExecutor
from ...services.validator import ValidationError, WorkflowValidator

WORKFLOW_NAME = "Example Workflow (demo)"

_PREPARE_SRC = """\
import json
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
    "data": {"done": True, "routes_taken": _input.get("routes", [])},
}))
"""


class Command(BaseCommand):
    help = "Create and optionally run an example DAG workflow."

    def add_arguments(self, parser):
        parser.add_argument("--run",     action="store_true", help="Execute the workflow after building it.")
        parser.add_argument("--destroy", action="store_true", help="Delete the example workflow and all its runs.")

    def handle(self, *args, **options):
        if options["destroy"]:
            self._destroy()
            return

        workflow = self._build()
        self._validate(workflow)

        if options["run"]:
            self._execute(workflow)
        else:
            self.stdout.write(self.style.SUCCESS(
                f"\nWorkflow '{workflow.name}' (id={workflow.id}, slug={workflow.slug}) built and validated.\n"
                f"Run with:  python manage.py example_workflow --run\n"
            ))

    def _destroy(self):
        deleted, _ = Workflow.objects.filter(name=WORKFLOW_NAME).delete()
        if deleted:
            self.stdout.write(self.style.WARNING(f"Deleted example workflow and {deleted} related objects."))
        else:
            self.stdout.write("No example workflow found.")

    def _build(self) -> Workflow:
        wf, created = Workflow.objects.get_or_create(
            name=WORKFLOW_NAME,
            defaults={"description": "Auto-generated demo workflow."},
        )

        if not created:
            self.stdout.write(f"Re-using existing workflow id={wf.id}")
            return wf

        self.stdout.write(f"Building workflow id={wf.id} ...")

        fn_prepare  = self._upsert_lambda("demo_prepare",  _PREPARE_SRC)
        fn_branch_a = self._upsert_lambda("demo_branch_a", _BRANCH_A_SRC)
        fn_branch_b = self._upsert_lambda("demo_branch_b", _BRANCH_B_SRC)
        fn_finalize = self._upsert_lambda("demo_finalize", _FINALIZE_SRC)

        n_prepare  = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="prepare",  lambda_function=fn_prepare,  position_x=0,    position_y=0)
        n_split    = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.SPLIT,  label="split",                                position_x=0,    position_y=100)
        n_a        = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="branch_a", lambda_function=fn_branch_a, position_x=-150, position_y=200)
        n_b        = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="branch_b", lambda_function=fn_branch_b, position_x=150,  position_y=200)
        n_join     = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.JOIN,   label="join",                                position_x=0,    position_y=300)
        n_finalize = WorkflowNode.objects.create(workflow=wf, node_type=WorkflowNode.LAMBDA, label="finalize", lambda_function=fn_finalize, position_x=0,    position_y=400)

        WorkflowEdge.objects.create(workflow=wf, source=n_prepare, target=n_split)
        WorkflowEdge.objects.create(workflow=wf, source=n_split,   target=n_a,       branch_key="path_a")
        WorkflowEdge.objects.create(workflow=wf, source=n_split,   target=n_b,       branch_key="path_b")
        WorkflowEdge.objects.create(workflow=wf, source=n_a,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_b,       target=n_join)
        WorkflowEdge.objects.create(workflow=wf, source=n_join,    target=n_finalize)

        self.stdout.write(self.style.SUCCESS("  ✓ 6 nodes, 6 edges created."))
        return wf

    def _upsert_lambda(self, name: str, src: str) -> LambdaFunction:
        fn, _ = LambdaFunction.objects.update_or_create(
            function_name=name,
            defaults={"content": src},
        )
        return fn

    def _validate(self, wf: Workflow):
        try:
            WorkflowValidator(wf).validate()
            self.stdout.write(self.style.SUCCESS("  ✓ Validation passed."))
        except ValidationError as exc:
            self.stderr.write(self.style.ERROR("Validation failed:"))
            for e in exc.errors:
                self.stderr.write(f"  • {e}")
            raise CommandError("Fix validation errors before running.")

    def _execute(self, wf: Workflow):
        run = WorkflowRun.objects.create(workflow=wf, input_data={"seed": 7})
        self.stdout.write(f"\nStarting run id={run.id} ...")
        WorkflowExecutor().start(run)
        run.refresh_from_db()
        self.stdout.write(f"\nRun status: {self.style.SUCCESS(run.status.upper())}")
        self.stdout.write("\nNode runs:")
        for nr in run.node_runs.select_related("node").order_by("id"):
            status_str = self.style.SUCCESS(nr.status) if nr.status == "completed" else self.style.WARNING(nr.status)
            self.stdout.write(f"  [{status_str}] {nr.node.label} ({nr.node.node_type})")
