"""The worker's entry point.

A workflow node rather than a bare `shared_task`, which is what texlab chose and
aralia copied, for the reason their comments give: nodes are autodiscovered by
`WorkflowsConfig.ready()`, so this needs **no entry in `TASK_MODULES`** and cannot
become the kind of task beat enqueues and the worker answers KeyError to.
"""

from toto.workflows.predefined_tasks import register

from .models import RunStatus


@register("steven_ask")
def steven_ask(input_data: dict) -> dict:
    from .runner import execute_run

    run_id = (input_data.get("data") or {}).get("run_id")
    if run_id is None:
        raise ValueError("steven_ask requires run_id in its input data.")

    run = execute_run(run_id)
    if run.status == RunStatus.FAILED:
        # Raise so the WorkflowRun shows FAILED too. execute_run already wrote
        # the sentence onto the row; this only propagates the verdict.
        raise RuntimeError(run.error or "The request failed.")
    return {"data": {"run_id": run_id, "status": run.status,
                     "tokens": run.total_tokens}}
