"""The nightly sweep, as a task.

A thin wrapper and nothing else: the work lives in `cleanup.py`, which imports
no celery, so a host with no worker runs exactly the same code straight from a
request. That is the tax sweep's doctrine and the reason "Run cleanup now"
works on a box that never started a worker.

`toto.forum` had to be added to `toto.registry.TASK_MODULES` for this to be
reachable at all — without that line beat enqueues the task forever and the
worker answers `KeyError`, which is how `toto.weather`'s refresh silently did
nothing for months. `toto.tests_schedules` asserts the pairing.
"""

from celery import shared_task


@shared_task(name="toto.forum.tasks.forum_cleanup", ignore_result=True,
             soft_time_limit=1740, time_limit=1800)
def forum_cleanup():
    """Delete forum history older than the staff-set retention period."""
    from . import cleanup

    # Our own deadline, comfortably inside celery's soft limit, so the run
    # closes itself as `partial` and records what it managed rather than being
    # killed mid-chunk with nothing written.
    return cleanup.run_scheduled(deadline_seconds=1500)


@shared_task(name="toto.forum.tasks.forum_cleanup_run", ignore_result=True,
             soft_time_limit=1740, time_limit=1800)
def forum_cleanup_run(run_id):
    """Finish a run somebody already claimed — the room Settings tab's button.

    Takes the run id rather than a channel id BECAUSE THE CLAIM ALREADY
    HAPPENED. `cleanup.trigger()` created the row inside a locked transaction
    and that is what stops two sweeps overlapping; a task that re-derived the
    channel and claimed again would race with the request that queued it, and
    the second claim would raise `CleanupInProgress` into a worker where
    nobody would read it.

    A missing row is not an error: the run can have been closed by the
    stuck-run sweeper, or the room deleted, between the request and the
    worker picking this up.
    """
    from . import cleanup
    from .models import ForumCleanupRun, RunStatus

    run = ForumCleanupRun.objects.filter(
        pk=run_id, status__in=(RunStatus.PENDING, RunStatus.RUNNING)).first()
    if run is None:
        return {"skipped": True, "run": run_id}
    cleanup.run_cleanup(run, deadline_seconds=1500)
    return {"run": run.pk, "status": run.status,
            "messages_deleted": run.messages_deleted}


@shared_task(name="toto.forum.tasks.forum_expire", ignore_result=True,
             soft_time_limit=240, time_limit=300)
def forum_expire():
    """Delete temporary rooms whose time is up, with everything in them."""
    from . import expiry

    return expiry.expire_due()
