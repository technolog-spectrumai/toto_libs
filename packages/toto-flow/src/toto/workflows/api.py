"""
Public API for triggering workflows programmatically from other apps.

Usage:
    from toto.workflows.api import trigger_workflow

    run = trigger_workflow("my-workflow-slug", {"key": "value"})
    # run is a WorkflowRun; execution happens asynchronously via Celery
"""
from __future__ import annotations

from .models import Workflow, WorkflowRun


def trigger_workflow(workflow_slug: str, payload: dict | None = None) -> WorkflowRun:
    """
    Start a workflow by slug and return the created WorkflowRun.

    Execution is asynchronous — the run is queued via Celery and this
    function returns immediately. Poll run.status or subscribe via the
    API endpoint to track completion.

    Raises Workflow.DoesNotExist if the slug is not found.
    """
    from .tasks import start_workflow_run_task

    workflow = Workflow.objects.get(slug=workflow_slug)
    run = WorkflowRun.objects.create(
        workflow=workflow,
        input_data=payload or {},
    )
    start_workflow_run_task.delay(run.id)
    return run
