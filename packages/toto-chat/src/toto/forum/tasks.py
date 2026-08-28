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
