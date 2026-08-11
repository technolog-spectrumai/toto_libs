"""Execute a GitRun (push/pull) and record its outcome — the fileservices
runner shape, minus staging (git ops act on the repo's worktree directly)."""

from __future__ import annotations

from django.utils import timezone

from . import git_cli, services
from .models import GitRun


def execute_git_run(run_id: int) -> None:
    run = GitRun.objects.select_related("repo", "user").get(pk=run_id)
    if run.status in (GitRun.SUCCESS, GitRun.FAILED):
        # A redelivered task (broker visibility timeout) must not resurrect a
        # row the stuck-run sweeper already closed.
        return
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
        # Deliberately NOT refunded. git ran, the export happened, and the
        # conflict list is the product — refunding it would make a conflicting
        # merge cheaper than a clean one.
        run.stderr = "merge conflicts (aborted): " + ", ".join(exc.paths)
        run.import_summary = {"conflicts": exc.paths}
        run.status = GitRun.FAILED
    except Exception as exc:
        run.stderr = (run.stderr + "\n" if run.stderr else "") + str(exc)
        run.status = GitRun.FAILED
        # This function is the single funnel for both execution paths — the
        # celery worker reaches it through predefined_tasks, and a host with no
        # worker calls it inline from dispatch_git_run — so one refund here
        # covers both. refund_for re-finds the record by (source_type,
        # source_id) precisely so a worker holding nothing but a pk can undo a
        # charge the web request posted.
        from toto.quota.charge import refund_for

        refund_for("gitvault.GitRun", run.pk, "gitvault.run",
                   reason=f"{run.op} failed: {exc}")

    run.finished_at = timezone.now()
    run.save()


def fail_run(run: GitRun, message: str) -> GitRun:
    """Close a run that will never report — the _dispatch_recorded shape,
    extracted so the dispatch error path and the stuck-run sweeper share one
    closer. A run closed while still PENDING never ran: its charge is
    refunded (paid-for-but-never-delivered)."""
    never_ran = run.status == GitRun.PENDING
    run.status = GitRun.FAILED
    run.stderr = (run.stderr + "\n" if run.stderr else "") + message
    run.finished_at = timezone.now()
    run.save(update_fields=["status", "stderr", "finished_at"])
    if never_ran:
        from toto.quota.charge import refund_for

        refund_for("gitvault.GitRun", run.pk, "gitvault.run",
                   reason=f"never ran: {message}")
    return run
