"""Create + dispatch a GitRun — celery when a worker answers, else inline
(the fileservices idiom, so push/pull work on celery-less deployments too)."""

from __future__ import annotations

from toto.celery_utils import celery_available

from .models import GitRepo, GitRun


def create_git_run(user, repo: GitRepo, op: str) -> GitRun:
    return GitRun.objects.create(repo=repo, user=user, op=op, status=GitRun.PENDING)


def dispatch_git_run(run: GitRun) -> bool:
    """True when queued asynchronously, False when executed inline."""
    if celery_available():
        from .tasks import run_git_task
        run_git_task.delay(run.id)
        return True

    from .runner import execute_git_run
    execute_git_run(run.id)
    return False
