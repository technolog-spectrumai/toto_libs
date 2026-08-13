"""Getting an ask onto a worker, or refusing it by name.

Three functions, in the order texlab established: `create_run` makes the row,
`dispatch_run` hands it to a worker, `fail_run` closes it. Nothing else creates
or closes an AiRun, so there is exactly one place a run can start and exactly one
where it can end badly.

**An inference is never run inline.** A completion takes seconds to tens of
seconds; doing that in the request holds a web worker for the whole time and the
user gets a spinner that is really a blocked socket. fileservices falls back to
inline and that is right for a fast local ffprobe; this is the aralia/texlab case
instead — if there is no worker, refuse and say which flag is missing, because a
refusal an operator can act on beats a timeout nobody can diagnose.
"""

from __future__ import annotations

from django.apps import apps
from django.utils import timezone

from .models import AiRun, RunStatus


class CannotQueue(Exception):
    """No worker, or no workflow engine."""


def workflows_installed() -> bool:
    return apps.is_installed("toto.workflows")


def create_run(*, user, surface: str, action: str, source_text: str,
               instruction: str = "") -> AiRun:
    """The row, before anything is dispatched — so the browser has something to
    poll even if the queueing itself fails."""
    return AiRun.objects.create(
        owner=user,
        surface=surface,
        action=action,
        source_text=source_text,
        instruction=instruction,
    )


def dispatch_run(run: AiRun) -> AiRun:
    """Queue it. Raises :class:`CannotQueue` naming what is missing."""
    if not workflows_installed():
        raise CannotQueue(
            "The assistant runs on a worker, and this build has none. "
            "Set BUILD_WORKFLOWS=1.")

    from toto.celery_utils import celery_available

    if not celery_available():
        raise CannotQueue(
            "No worker is listening. Start one, and ask again.")

    from toto.workflows.models import WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    from .workflow import ensure_ask_workflow

    workflow_run = WorkflowRun.objects.create(
        workflow=ensure_ask_workflow(),
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
    """Close a row that will never finish. Idempotent; also the sweeper's closer.

    **No refund, and none is needed.** Everything else on this platform pays
    before it works and therefore has to unwind; the assistant charges only after
    a successful answer, so a run that never finished has never been billed. That
    is the whole benefit of charging afterwards, and it is why this function is
    six lines rather than aralia's twenty.
    """
    if isinstance(run, int):
        run = AiRun.objects.filter(pk=run).first()
        if run is None:
            return
    if run.is_finished:
        return
    run.status = RunStatus.FAILED
    run.error = reason or "This request was closed without finishing."
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "error", "finished_at"])
