"""The nightly cleanup and the temporary-room expiry, as tasks.

`forum_cleanup` keeps its name and its module on purpose: monit's heartbeats
record by task name, and `toto.tests_schedules` pairs the beat entry with
`toto.registry.TASK_MODULES`. `toto.forum` had to be added to TASK_MODULES for
this to be reachable at all — without that line beat enqueues the task forever
and the worker answers `KeyError`, which is how `toto.weather`'s refresh
silently did nothing for months.

Since 2026-10-02 the beat task does not sweep in-process. It CLAIMS the night's
passes (one per room with its own enabled dial, plus the platform pass for
every other room) and hands them to the worker as ONE run of the "Forum
cleanup" workflow, so the night shows in the Workflows tab beside every manual
cleanup, started by "System". The deleting happens in that workflow's node
(`predefined_tasks.py`), on the worker, inside its own deadline. There is no
inline path left anywhere: a web request never deletes history itself.

The former `forum_cleanup_run(run_id)` task — the room button's door onto the
worker — is gone: every button now dispatches the workflow, and a task that
finished any RUNNING row by id was one more door than the design needs.
"""

from celery import shared_task


@shared_task(name="toto.forum.tasks.forum_cleanup", ignore_result=True,
             soft_time_limit=1740, time_limit=1800)
def forum_cleanup():
    """Queue tonight's cleanup: claim the passes, dispatch one workflow run.

    Claiming is a handful of rows and returns at once. The long limits are for
    the one other branch: on a worker whose build has no workflow engine the
    passes are finished right here instead — still on the worker, never in a
    web request — so the library stays usable on its own.
    """
    from . import cleanup, dispatch

    if not dispatch.workflows_installed():
        return cleanup.run_scheduled(
            deadline_seconds=cleanup.WORKER_DEADLINE_SECONDS)
    return dispatch.start_scheduled()


@shared_task(name="toto.forum.tasks.forum_expire", ignore_result=True,
             soft_time_limit=240, time_limit=300)
def forum_expire():
    """Delete temporary rooms whose time is up, with everything in them."""
    from . import expiry

    return expiry.expire_due()
