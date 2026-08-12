"""Create + dispatch a GitRun through the workflows engine (fileservices
pattern, three execution paths):

1. celery up + "repo-run" Workflow seeded (ingress_repo) → wrap in a
   WorkflowRun so the run is visible in the workflows UI; the executor runs
   the `repo_run` predefined task inline on the celery worker.
2. celery up, workflow row missing → bare `run_git_task` fallback.
3. no celery worker → inline execution (works on celery-less deployments).
"""

from __future__ import annotations

from toto.celery_utils import celery_available

from .models import GitRepo, GitRun

WORKFLOW_SLUG = "repo-run"


def create_git_run(user, repo: GitRepo, op: str) -> GitRun:
    """Make the run row, metering it on the way.

    The guard lives down here rather than in the views because both callers —
    `init_repo` and `_dispatch_op` — already funnel through this one function,
    so putting it here covers every path by construction instead of by
    remembering. Raises QuotaExceeded / InsufficientFunds; `_json_errors` in
    views.py turns both into 429 / 402.
    """
    from toto.quota import check_quota, record_usage
    from toto.quota.charge import charge, check_funds, price_for

    from .models import RepoQuotaPolicy, RepoUsageEvent

    tariff = price_for(user, "repo")
    check_quota(RepoQuotaPolicy, "repo.run", 1, user)
    check_funds(user, tariff, "repo.run", 1)

    run = GitRun.objects.create(repo=repo, user=user, op=op, status=GitRun.PENDING)

    src = {"source_type": "repo.GitRun", "source_id": str(run.pk)}
    if record_usage(RepoUsageEvent, "repo.run", 1, user,
                    source_label=op, idempotency_key=f"repo.run:{run.pk}",
                    **src) is not None:
        charge(user, tariff, "repo.run", 1, **src)
    return run


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
                started_by=run.user,
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
