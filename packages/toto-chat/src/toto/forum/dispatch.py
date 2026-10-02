"""Getting a cleanup onto the worker, or refusing it by name.

The antivirus order: check, claim, dispatch, and close on a failed dispatch.
Nothing else hands a cleanup to a worker, and since 2026-10-02 nothing runs
one anywhere else — the inline 25-second run in the web request is gone.

1. **Check BEFORE claiming.** With no workflow engine or no worker listening,
   :class:`CannotQueue` is raised before any row exists, so a refused press
   leaves nothing RUNNING behind to block the next one.
2. **Claim** (`cleanup.trigger` for one room, `cleanup.claim_passes` for what
   the night runs). The claim is the lock: it is what stops two sweeps
   overlapping, and the worker never claims anything itself.
3. **Dispatch** one run of the "Forum cleanup" workflow covering every claimed
   row. Each row gets its `workflow_run_id` BEFORE `.delay`, and the node
   finishes only rows carrying its own run's id — so the node can do nothing
   that staff or the schedule did not already claim.
4. If the dispatch fails after the claim, the rows are closed with
   `cleanup.fail_run`, so `in_flight()` never sticks on a cleanup that was
   never queued.

NOT BILLED, deliberately: the WorkflowRun is created here, never through the
Workflows API's start door, which is the one place a run is priced. Cleanup is
platform housekeeping that staff or the schedule start, not a member's job.
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
    """Raise :class:`CannotQueue` unless a cleanup could be queued right now.

    `need_worker=False` is for the beat task, which is already running ON a
    worker: asking whether one is listening would ask that very worker, and a
    solo-pool worker busy running the question cannot answer it.
    """
    if not workflows_installed():
        raise CannotQueue(
            "Forum cleanup runs on a worker through the workflow engine, and "
            "this build has none. Install toto.workflows.")
    if need_worker:
        from toto.celery_utils import celery_available

        if not celery_available():
            raise CannotQueue(
                _("No worker is listening. Start one, and try again."))


def start_room(channel, user):
    """The room Settings tab's button: claim this room, queue it.

    Raises :class:`CannotQueue` (nothing claimed) or
    `cleanup.CleanupInProgress` (nothing claimed).
    """
    from .models import TriggeredBy

    check_can_queue()
    run = cleanup.trigger(triggered_by=TriggeredBy.MANUAL, user=user,
                          channel=channel)
    dispatch([run], started_by=user)
    return run


def start_forum(user):
    """The Cleanup page's button: what the night runs, started by a person.

    All or nothing — `cleanup.claim_passes(manual=True)` refuses while any
    cleanup is live. Returns the claimed runs (none only if there was nothing
    to claim at all).
    """
    from .models import TriggeredBy

    check_can_queue()
    runs, _skipped = cleanup.claim_passes(triggered_by=TriggeredBy.MANUAL,
                                          user=user, manual=True)
    if runs:
        dispatch(runs, started_by=user)
    return runs


def start_scheduled() -> dict:
    """The nightly beat: claim the passes, queue ONE workflow run for them.

    Skips silently while every dial is off, and skips a pass already in
    flight. `started_by` is None, which the Workflows tab shows as the system.
    """
    from .models import TriggeredBy

    check_can_queue(need_worker=False)
    runs, skipped = cleanup.claim_passes(triggered_by=TriggeredBy.BEAT)
    if not runs:
        return {"skipped": "in_progress" if skipped else "disabled",
                "passes": skipped}
    workflow_run = dispatch(runs, started_by=None)
    return {"workflow_run": workflow_run.pk,
            "cleanup_runs": [run.pk for run in runs],
            "passes": skipped}


def dispatch(runs, *, started_by=None):
    """Queue one "Forum cleanup" workflow run covering these claimed rows.

    Returns the WorkflowRun. On any failure the rows are closed FAILED, the
    WorkflowRun (if it was created) is marked FAILED, and
    :class:`CannotQueue` is raised.
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
        # with its input and nothing else — this is how the node tells its
        # own rows from anybody else's.
        workflow_run.input_data = {"data": {
            "cleanup_run_ids": ids, "workflow_run_id": workflow_run.pk}}
        workflow_run.save(update_fields=["input_data"])
        ForumCleanupRun.objects.filter(pk__in=ids).update(
            workflow_run_id=workflow_run.pk)

        result = start_workflow_run_task.delay(workflow_run.pk)
        ForumCleanupRun.objects.filter(pk__in=ids).update(
            task_id=(getattr(result, "id", "") or "")[:255])
    except Exception as exc:  # noqa: BLE001 — a claim must never outlive a failed queue
        reason = _("The cleanup could not be handed to the worker: %(error)s") % {
            "error": exc}
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
    """The engine's own closer: WorkflowRun has no error field, so the reason
    lives on the cleanup rows and in the log."""
    try:
        from toto.workflows.services.executor import fail_stuck_workflow_run

        fail_stuck_workflow_run(workflow_run, reason)
    except Exception:  # noqa: BLE001 — the rows are what must be closed
        import logging

        logging.getLogger(__name__).exception(
            "forum cleanup: could not mark workflow run %s failed",
            workflow_run.pk)
