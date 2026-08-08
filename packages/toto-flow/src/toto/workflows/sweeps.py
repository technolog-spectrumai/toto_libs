"""Workflows' stuck-run sweep policies. Pure data — see toto.quota.sweeps.

Two policies, node-run first: declaration order is sweep order, and the node
closer cascades to its run, so by the time the run policy looks, cascaded
runs are already FAILED. Node runs reference started_at only (the model has
no created_at); a PENDING async-lambda node with a null started_at is skipped
by the all-references-null guard and covered transitively by the run policy.
Floors: nodes run under the global 1800 s hard limit (lambda nodes tighter);
the run floor is strictly above the node floor so the cascade gets first
crack.
"""

from toto.quota.sweeps import StuckRunPolicy, register

register(StuckRunPolicy(
    model_label="workflows.WorkflowNodeRun",
    active_values=("pending", "running"),
    closer="toto.workflows.services.executor.fail_stuck_node_run",
    cutoff_seconds=10800,
    reference_fields=("started_at",),
    task_id_field="celery_task_id",   # async lambda + async predefined nodes only
))

register(StuckRunPolicy(
    model_label="workflows.WorkflowRun",
    active_values=("pending", "running"),
    closer="toto.workflows.services.executor.fail_stuck_workflow_run",
    cutoff_seconds=21600,
    reference_fields=("started_at", "created_at"),
))
