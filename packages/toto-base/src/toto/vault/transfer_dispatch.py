"""Getting vault's queued jobs onto a worker, or refusing them by name.

The texlab order: ``create_*`` makes the row, ``dispatch_*`` hands it to a
worker, ``fail_*`` closes it. Nothing else creates or closes a run. Two run
kinds live here — the mirror refresh and the transfer — because they share
one doctrine:

**Refuse, never inline.** Both are network-bound against another host (or
S3) — the steven case, not the fileservices case. Running either inline
would park a web worker on someone else's latency, so a build with no
worker gets a sentence naming the flag instead of a degraded mode.
"""
from __future__ import annotations

from django.apps import apps
from django.utils import timezone
from django.utils.translation import gettext as _

from .mirror import BucketRefreshRun, RefreshStatus
from .transfer import TransferRun, TransferStatus


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
        raise CannotQueue(_("No worker is listening. Start one, and try again."))

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


def create_transfer_run(*, user, source_bucket, dest_bucket, dest_directory,
                        files, copy_policy) -> TransferRun:
    """The row, frozen: ids, denominator and the estimate the pre-flight
    affordability checks were made against."""
    return TransferRun.objects.create(
        owner=user,
        source_bucket=source_bucket,
        dest_bucket=dest_bucket,
        dest_directory=dest_directory,
        file_ids=[f.pk for f in files],
        copy_policy=copy_policy,
        total_files=len(files),
        bytes_estimated=sum(f.file_size_bytes or 0 for f in files),
    )


def dispatch_transfer_run(run: TransferRun) -> TransferRun:
    """Queue it. Raises :class:`CannotQueue` naming what is missing."""
    if not workflows_installed():
        raise CannotQueue(
            "A transfer crosses hosts and runs on a worker, and this build "
            "has none. Set BUILD_WORKFLOWS=1.")

    from toto.celery_utils import celery_available

    if not celery_available():
        raise CannotQueue(_("No worker is listening. Start one, and try again."))

    from toto.workflows.models import WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    from .workflow import ensure_transfer_workflow

    workflow_run = WorkflowRun.objects.create(
        workflow=ensure_transfer_workflow(),
        input_data={"data": {"run_id": run.pk}},
        started_by=run.owner,
    )
    run.workflow_run_id = workflow_run.pk
    run.save(update_fields=["workflow_run_id"])

    result = start_workflow_run_task.delay(workflow_run.pk)
    run.task_id = getattr(result, "id", "") or ""
    run.save(update_fields=["task_id"])
    return run


def fail_transfer_run(run, reason: str = "") -> None:
    """Close a row that will never finish. Idempotent; the sweeper's closer.

    No refund logic here: a transfer bills per LANDED file, so a run that
    stopped early was billed exactly for what landed."""
    if isinstance(run, int):
        run = TransferRun.objects.filter(pk=run).first()
        if run is None:
            return
    if run.is_finished:
        return
    run.status = TransferStatus.FAILED
    run.error = reason or "This transfer was closed without finishing."
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
