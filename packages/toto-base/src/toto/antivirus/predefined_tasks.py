"""The worker's entry point.

A workflow node rather than a bare ``shared_task`` — nodes are autodiscovered
by ``WorkflowsConfig.ready()``, so this needs no ``TASK_MODULES`` entry and
cannot become a task beat enqueues that the worker answers KeyError to.
"""

from toto.workflows.predefined_tasks import register

from .models import RunStatus


@register("antivirus_scan", dispatch_only=True)
def antivirus_scan(input_data: dict) -> dict:
    from .runner import execute_run

    run_id = (input_data.get("data") or {}).get("run_id")
    if run_id is None:
        raise ValueError("antivirus_scan requires run_id in its input data.")

    run = execute_run(run_id)
    if run.status == RunStatus.FAILED:
        # Raise so the WorkflowRun shows FAILED too; the sentence is already
        # on the row.
        raise RuntimeError(run.error or "The scan failed.")
    return {"data": {"run_id": run_id, "status": run.status}}
