"""Execute a GitRun (push/pull) and record its outcome — the fileservices
runner shape, minus staging (git ops act on the repo's worktree directly)."""

from __future__ import annotations

from django.utils import timezone

from . import git_cli, services
from .models import GitRun


def execute_git_run(run_id: int) -> None:
    run = GitRun.objects.select_related("repo", "user").get(pk=run_id)
    run.status = GitRun.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    try:
        op = {
            "init": services.run_init,
            "push": services.run_push,
            "pull": services.run_pull,
        }[run.op]
        result = op(run.repo, run.user)
        run.stdout = result["stdout"]
        run.stderr = result["stderr"]
        run.import_summary = result["import_summary"]
        run.status = GitRun.SUCCESS
    except git_cli.MergeConflict as exc:
        run.stderr = "merge conflicts (aborted): " + ", ".join(exc.paths)
        run.import_summary = {"conflicts": exc.paths}
        run.status = GitRun.FAILED
    except Exception as exc:
        run.stderr = (run.stderr + "\n" if run.stderr else "") + str(exc)
        run.status = GitRun.FAILED

    run.finished_at = timezone.now()
    run.save()
