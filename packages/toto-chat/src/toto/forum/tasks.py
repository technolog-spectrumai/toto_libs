"""The nightly cleanup, as a task.

``forum_cleanup`` keeps its name and its module on purpose: monit's
heartbeats record by task name, and ``toto.tests_schedules`` pairs the beat
entry (``toto.schedules.beat_schedule(forum_cleanup=True)``) with
``toto.registry.TASK_MODULES``, which names ``toto.forum`` so that the
worker finds this module at all.

The task does not remove anything itself. It reads the forum's settings
first and returns at once, touching no row, while the scheduled cleanup is
switched off (``ForumSettings.retention_enabled``, the Settings page's
switch: the ONE switch, with no deploy-time flag beside it). Switched on, it
CLAIMS one forum-wide cleanup at the retention age and hands it to the
worker as ONE run of the "Forum cleanup" workflow, so the night shows in the
Workflows tab beside every cleanup an administrator started, as started by
the system. The removing happens in that workflow's node
(``predefined_tasks.py``), on the worker, inside its own deadline.
"""

from celery import shared_task


@shared_task(name="toto.forum.tasks.forum_cleanup", ignore_result=True,
             soft_time_limit=1740, time_limit=1800)
def forum_cleanup():
    """Queue tonight's cleanup: claim it, dispatch one workflow run.

    Claiming is one row and returns at once. The long limits are for the one
    other branch: on a worker whose build has no workflow engine the cleanup
    is finished right here instead — still on the worker, never in a web
    request.
    """
    from . import cleanup, dispatch

    if not dispatch.workflows_installed():
        return cleanup.run_scheduled(deadline_seconds=cleanup.WORKER_DEADLINE_SECONDS)
    return dispatch.start_scheduled()
