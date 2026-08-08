"""
Shared helpers to create and dispatch a :class:`FileServiceRun`.

Used by ``run_service`` (the vault wand) and by the per-app builder pages
so they all enqueue work the same way.
"""

from __future__ import annotations

from toto.celery_utils import celery_available

from .models import FileServiceRun


def create_service_run(user, vault_file, service_key: str, args: str = "") -> FileServiceRun:
    return FileServiceRun.objects.create(
        service_key=service_key,
        owner=user,
        input_file=vault_file,
        bucket=vault_file.bucket,
        args=args,
        status=FileServiceRun.PENDING,
    )


def dispatch_run(run: FileServiceRun) -> bool:
    """Dispatch a run.

    Returns True when queued asynchronously, False when executed inline.
    Inline execution may raise; callers should handle it.

    A run whose owner raised the "fileservices.run_runtime" dial takes the
    direct task with explicit celery limits: the workflow wrapper does its
    work inside the engine's own tasks, which per-run limits cannot reach, so
    an extended run trades the WorkflowRun tracking row for a budget that is
    actually honored.
    """
    from toto.quota import times

    budget = times.effective_seconds("fileservices.run_runtime", user=run.owner)
    extended = budget > times.free_seconds("fileservices.run_runtime")

    if celery_available():
        from toto.workflows.models import Workflow, WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task

        wf = None if extended else Workflow.objects.filter(slug="fileservices-run").first()
        if wf is not None:
            wf_run = WorkflowRun.objects.create(
                workflow=wf,
                input_data={"data": {"run_id": run.id}},
            )
            run.workflow_run = wf_run
            run.save(update_fields=["workflow_run"])
            start_workflow_run_task.delay(wf_run.pk)
        else:
            from .tasks import run_file_service_task
            if extended:
                run_file_service_task.apply_async(
                    args=[run.id],
                    soft_time_limit=budget, time_limit=budget + 100)
            else:
                run_file_service_task.delay(run.id)
        return True

    from .runner import execute_run
    execute_run(run.id)
    return False


def fail_run(run: FileServiceRun, message: str) -> FileServiceRun:
    """Terminally close a run that will never report — used by the dispatch
    error path and the stuck-run sweeper."""
    from django.utils import timezone

    run.status = FileServiceRun.FAILED
    run.stderr = (run.stderr + "\n" if run.stderr else "") + message
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "stderr", "finished_at"])
    return run
