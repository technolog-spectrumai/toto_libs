"""Workflow-engine entrypoint for git runs (init/push/pull) — the
fileservices `fileservice_run` shape.

The GitRun row stays the repo UI's source of truth (execute_git_run
records status/stdout/stderr there and never raises); the re-raise below only
propagates failure to the executor so the linked WorkflowRun shows FAILED in
the workflows UI instead of a false COMPLETED.
"""

from toto.workflows.predefined_tasks import register


@register("repo_run")
def repo_run(input_data: dict) -> dict:
    from .models import GitRun
    from .runner import execute_git_run

    data = input_data.get("data") or {}
    run_id = data.get("run_id")
    if run_id is None:
        raise ValueError("repo_run requires run_id in input data.")

    execute_git_run(run_id)

    run = GitRun.objects.get(pk=run_id)
    if run.status == GitRun.FAILED:
        raise RuntimeError(run.stderr or f"git {run.op} failed")
    return {
        "data": {
            "run_id": run_id,
            "op": run.op,
            "status": run.status,
            "import_summary": run.import_summary,
        }
    }
