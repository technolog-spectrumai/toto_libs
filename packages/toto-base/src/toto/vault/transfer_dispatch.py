"""Getting a mirror refresh onto a worker, or refusing it by name.

The texlab order: ``create_*`` makes the row, ``dispatch_*`` hands it to a
worker, ``fail_*`` closes it. Nothing else creates or closes a run.

**Refuse, never inline.** A refresh is network-bound against another host —
the steven case, not the fileservices case. Running it inline would park a
web worker on a peer's latency for as long as the listing takes, so a build
with no worker gets a sentence naming the flag instead of a degraded mode.
(Stage 5's transfer runs will share this module for the same reason.)
"""
from __future__ import annotations

from django.apps import apps
from django.utils import timezone

from .mirror import BucketRefreshRun, RefreshStatus


class CannotQueue(Exception):
    """No worker, or no workflow engine. The message names what is missing."""


def workflows_installed() -> bool:
    return apps.is_installed("toto.workflows")


def create_refresh_run(*, user, bucket) -> BucketRefreshRun:
    """The row, before anything is dispatched — so the browser has something
    to poll even if the queueing itself fails."""
    return BucketRefreshRun.objects.create(owner=user, bucket=bucket)


def dispatch_refresh_run(run: BucketRefreshRun) -> BucketRefreshRun:
    """Queue it. Raises :class:`CannotQueue` naming what is missing."""
    if not workflows_installed():
        raise CannotQueue(
            "A mirror refresh talks to another host and runs on a worker, "
            "and this build has none. Set BUILD_WORKFLOWS=1.")

    from toto.celery_utils import celery_available

    if not celery_available():
        raise CannotQueue("No worker is listening. Start one, and try again.")

    from toto.workflows.models import WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    from .workflow import ensure_refresh_workflow

    workflow_run = WorkflowRun.objects.create(
        workflow=ensure_refresh_workflow(),
        input_data={"data": {"run_id": run.pk}},
        started_by=run.owner,
    )
    run.workflow_run_id = workflow_run.pk
    run.save(update_fields=["workflow_run_id"])

    result = start_workflow_run_task.delay(workflow_run.pk)
    run.task_id = getattr(result, "id", "") or ""
    run.save(update_fields=["task_id"])
    return run


def fail_refresh_run(run, reason: str = "") -> None:
    """Close a row that will never finish. Idempotent; the sweeper's closer."""
    if isinstance(run, int):
        run = BucketRefreshRun.objects.filter(pk=run).first()
        if run is None:
            return
    if run.is_finished:
        return
    run.status = RefreshStatus.FAILED
    run.error = reason or "This refresh was closed without finishing."
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
