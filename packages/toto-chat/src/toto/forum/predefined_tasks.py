"""The worker's entry point for every forum cleanup.

A workflow node rather than a bare ``shared_task``: nodes are autodiscovered by
``WorkflowsConfig.ready()``, and running through the engine is what puts each
cleanup in the Workflows tab, with who started it.

IT ONLY FINISHES WHAT WAS ALREADY CLAIMED. The input names cleanup rows and the
workflow run that carries them; a row is touched only if it is still PENDING or
RUNNING AND its `workflow_run_id` is that same run — written by `dispatch.py`
before the task was queued. Unknown, finished or foreign ids are ignored. It
never claims, never derives a boundary and never calls `run_scheduled`, so a
crafted input can delete nothing that staff or the schedule did not already
authorise. The node is also registered ``dispatch_only``: the Workflows API
refuses to start this workflow by hand, staff included.
"""

from toto.workflows.predefined_tasks import register


def _ids(raw) -> list[int]:
    out = []
    for value in raw if isinstance(raw, list) else []:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
            out.append(int(value))
    return out


@register("forum_cleanup", dispatch_only=True)
def forum_cleanup(input_data: dict) -> dict:
    from . import cleanup
    from .models import ForumCleanupRun, RunStatus

    data = (input_data or {}).get("data") or {}
    ids = _ids(data.get("cleanup_run_ids"))
    workflow_run_id = data.get("workflow_run_id")
    if isinstance(workflow_run_id, bool) or not isinstance(workflow_run_id, int):
        # Without its own run's id the node cannot tell its rows from anybody
        # else's, and `workflow_run_id=None` would match every unqueued row.
        return {"data": {"cleanup_run_ids": ids, "finished": 0,
                         "ignored": len(ids), "runs": []}}

    rows = {row.pk: row for row in ForumCleanupRun.objects.filter(
        pk__in=ids, workflow_run_id=workflow_run_id,
        status__in=(RunStatus.PENDING, RunStatus.RUNNING))
        .select_related("channel")}
    # In the dispatcher's order: the rooms' own passes first, the wide one last.
    runs = [rows[pk] for pk in dict.fromkeys(ids) if pk in rows]

    results = cleanup.finish(
        runs, deadline_seconds=cleanup.WORKER_DEADLINE_SECONDS)
    return {"data": {
        "cleanup_run_ids": ids,
        "finished": len(runs),
        "ignored": len(ids) - len(runs),
        "messages_deleted": sum(r.get("messages", 0) for r in results),
        "bytes_freed": sum(r.get("bytes", 0) for r in results),
        "runs": results,
    }}
