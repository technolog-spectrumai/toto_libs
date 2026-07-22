"""Create + dispatch a GitRun through the workflows engine (fileservices
pattern, three execution paths):

1. celery up + "gitvault-run" Workflow seeded (ingress_gitvault) → wrap in a
   WorkflowRun so the run is visible in the workflows UI; the executor runs
   the `gitvault_run` predefined task inline on the celery worker.
2. celery up, workflow row missing → bare `run_git_task` fallback.
3. no celery worker → inline execution (works on celery-less deployments).
"""

from __future__ import annotations

from toto.celery_utils import celery_available

from .models import GitRepo, GitRun

WORKFLOW_SLUG = "gitvault-run"


def create_git_run(user, repo: GitRepo, op: str) -> GitRun:
    return GitRun.objects.create(repo=repo, user=user, op=op, status=GitRun.PENDING)


def dispatch_git_run(run: GitRun) -> bool:
    """True when queued asynchronously, False when executed inline.
    Inline execution records failures on the GitRun and does not raise."""
    if celery_available():
        from toto.workflows.models import Workflow, WorkflowRun
        from toto.workflows.tasks import start_workflow_run_task

        wf = Workflow.objects.filter(slug=WORKFLOW_SLUG).first()
        if wf is not None:
            wf_run = WorkflowRun.objects.create(
                workflow=wf,
                input_data={"data": {"run_id": run.id}},
            )
            run.workflow_run = wf_run
            run.save(update_fields=["workflow_run"])
            start_workflow_run_task.delay(wf_run.pk)
        else:
            from .tasks import run_git_task
            run_git_task.delay(run.id)
        return True

    from .runner import execute_git_run
    execute_git_run(run.id)
    return False
