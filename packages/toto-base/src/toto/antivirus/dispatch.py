"""Getting a scan onto a worker, or refusing it by name.

The texlab order: ``create_run`` makes the row, ``dispatch_run`` hands it to a
worker, ``fail_run`` closes it. Nothing else creates or closes a ScanRun.

An on-demand scan is never run inline in a web worker — not because it is slow
(it is not) but because it is BILLED work with a run row, and one code path
for queued-and-charged keeps the record honest. The DOOR scans stay inline;
a refusal there decides whether a save happens at all.
"""

from __future__ import annotations

from django.apps import apps
from django.utils import timezone

from .models import RunStatus, ScanRun


class CannotQueue(Exception):
    """No worker, or no workflow engine."""


def workflows_installed() -> bool:
    return apps.is_installed("toto.workflows")


def create_run(*, user, vault_file) -> ScanRun:
    """The row, before anything is dispatched — so the browser has something
    to poll even if the queueing itself fails."""
    return ScanRun.objects.create(owner=user, file=vault_file)


def dispatch_run(run: ScanRun) -> ScanRun:
    """Queue it. Raises :class:`CannotQueue` naming what is missing."""
    if not workflows_installed():
        raise CannotQueue(
            "On-demand scans run on a worker, and this build has none. "
            "Set BUILD_WORKFLOWS=1.")

    from toto.celery_utils import celery_available

    if not celery_available():
        raise CannotQueue("No worker is listening. Start one, and try again.")

    from toto.workflows.models import WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    from .workflow import ensure_scan_workflow

    workflow_run = WorkflowRun.objects.create(
        workflow=ensure_scan_workflow(),
        input_data={"data": {"run_id": run.pk}},
        started_by=run.owner,
    )
    run.workflow_run_id = workflow_run.pk
    run.save(update_fields=["workflow_run_id"])

    result = start_workflow_run_task.delay(workflow_run.pk)
    run.task_id = getattr(result, "id", "") or ""
    run.save(update_fields=["task_id"])
    return run


def fail_run(run, reason: str = "") -> None:
    """Close a row that will never finish. Idempotent; the sweeper's closer.

    No refund needed: a scan charges only after a delivered verdict, so a run
    that never finished was never billed.
    """
    if isinstance(run, int):
        run = ScanRun.objects.filter(pk=run).first()
        if run is None:
            return
    if run.is_finished:
        return
    run.status = RunStatus.FAILED
    run.error = reason or "This scan was closed without finishing."
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
