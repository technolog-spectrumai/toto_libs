"""Getting a cleanup onto the worker, or refusing it by name.

The order is the antivirus's: check, claim, dispatch, and close on a failed
dispatch. Nothing else hands a cleanup to a worker, and nothing runs one in
a web request.

1. **Check BEFORE claiming.** With no workflow engine or no worker
   listening, ``CannotQueue`` is raised before any record exists, so a
   refused press leaves nothing RUNNING behind to block the next one.
2. **Claim** (``cleanup.claim``). The claim is the lock: it stops two
   cleanups overlapping, and the worker never claims anything itself.
3. **Dispatch** one run of the "Forum cleanup" workflow for the claimed
   record. The record gets its ``workflow_run_id`` BEFORE the task is queued,
   and the node finishes only records carrying its own run's id, so the node
   can do nothing that an administrator or the schedule did not claim.
4. If the dispatch fails after the claim, the record is closed with
   ``cleanup.fail_run``, so ``in_flight()`` never sticks on a cleanup that
   was never queued.

NOT BILLED, deliberately: the WorkflowRun is made here, never through the
Workflows API's start door, which is the one place a run is priced. Cleanup
is the platform's housekeeping, not a member's job.
"""

from __future__ import annotations

from django.apps import apps
from django.utils.translation import gettext as _

from . import cleanup


class CannotQueue(Exception):
    """No worker, or no workflow engine. The message names what is missing."""


def workflows_installed() -> bool:
    return apps.is_installed("toto.workflows")


def check_can_queue(*, need_worker: bool = True) -> None:
    """Raise ``CannotQueue`` unless a cleanup could be queued right now.

    ``need_worker=False`` is for the beat task, which is already running ON
    a worker: asking whether one is listening would ask that very worker.
    """
    if not workflows_installed():
        raise CannotQueue(_("Cleanup runs on the worker through the workflow engine, and "
                            "this server has none."))
    if need_worker:
        from toto.celery_utils import celery_available

        if not celery_available():
            raise CannotQueue(_("No worker is listening, so nothing was removed. Start "
                                "one, and try again."))


def start_manual(user, *, channel=None, days=None):
    """An administrator's cleanup: one channel or every channel (None), what
    is older than ``days`` days or everything (None). Returns the claimed
    run. Raises ``CannotQueue`` or ``cleanup.CleanupInProgress``; nothing is
    claimed then."""
    from .models import TriggeredBy

    check_can_queue()
    run = cleanup.claim(boundary=cleanup.boundary_for(days), retention_days=days or 0,
                        triggered_by=TriggeredBy.MANUAL, user=user, channel=channel)
    dispatch([run], started_by=user)
    return run


def start_scheduled() -> dict:
    """The nightly beat: claim the forum-wide cleanup at the retention age
    and queue one workflow run for it. Does nothing, and touches no row,
    while retention is switched off. ``started_by`` is None, which the
    Workflows tab shows as the system."""
    if cleanup.scheduled_settings() is None:
        return {"skipped": "disabled"}
    check_can_queue(need_worker=False)
    run = cleanup.claim_scheduled()
    if run is None:
        return {"skipped": "in_progress"}
    workflow_run = dispatch([run], started_by=None)
    return {"workflow_run": workflow_run.pk, "cleanup_runs": [run.pk]}


def dispatch(runs, *, started_by=None):
    """Queue one "Forum cleanup" workflow run for these claimed records.

    Returns the WorkflowRun. On any failure the records are closed FAILED,
    the WorkflowRun (if it was made) is marked FAILED, and ``CannotQueue``
    is raised.
    """
    from .models import ForumCleanupRun

    runs = list(runs)
    ids = [run.pk for run in runs]
    workflow_run = None
    try:
        from toto.workflows.models import WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task

        from .workflow import ensure_cleanup_workflow

        workflow_run = WorkflowRun.objects.create(
            workflow=ensure_cleanup_workflow(),
            input_data={"data": {"cleanup_run_ids": ids}},
            started_by=started_by if getattr(started_by, "pk", None) else None,
        )
        # The run's own id goes into its input, because a node is called
        # with its input and nothing else: this is how the node tells its
        # own records from anybody else's.
        workflow_run.input_data = {"data": {
            "cleanup_run_ids": ids, "workflow_run_id": workflow_run.pk}}
        workflow_run.save(update_fields=["input_data"])
        ForumCleanupRun.objects.filter(pk__in=ids).update(workflow_run_id=workflow_run.pk)

        result = start_workflow_run_task.delay(workflow_run.pk)
        ForumCleanupRun.objects.filter(pk__in=ids).update(
            task_id=str(getattr(result, "id", "") or "")[:255])
    except Exception as exc:  # noqa: BLE001 - a claim must never outlive a failed queue
        reason = _("The cleanup could not be handed to the worker: %(error)s") % {
            "error": str(exc) or type(exc).__name__}
        for run in ForumCleanupRun.objects.filter(pk__in=ids):
            if not run.is_finished:
                cleanup.fail_run(run, reason)
        if workflow_run is not None:
            _fail_workflow_run(workflow_run, str(reason))
        raise CannotQueue(reason) from exc
    for run in runs:
        run.workflow_run_id = workflow_run.pk
    return workflow_run


def _fail_workflow_run(workflow_run, reason: str) -> None:
    """The engine's own closer: a WorkflowRun has no error field, so the
    reason lives on the cleanup records and in the log."""
    try:
        from toto.workflows.services.executor import fail_stuck_workflow_run

        fail_stuck_workflow_run(workflow_run, reason)
    except Exception:  # noqa: BLE001 - the records are what must be closed
        import logging

        logging.getLogger(__name__).exception(
            "forum cleanup: could not mark workflow run %s failed", workflow_run.pk)
