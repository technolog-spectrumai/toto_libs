"""quota's one task. The app owns no tables; a task needs none.

The module name is a hard contract: autodiscovery imports ``<label>.tasks``
and nothing else, and "toto.quota" must stay in ``toto.registry.TASK_MODULES``.
The beat entry (``schedules.beat_schedule(sweep=...)``) is what turns it on.
"""

import logging

from celery import shared_task

log = logging.getLogger("toto.quota.sweeps")


@shared_task(name="toto.quota.tasks.sweep_stuck_runs", ignore_result=True,
             soft_time_limit=300, time_limit=360)
def sweep_stuck_runs() -> dict:
    from .sweeps import run_sweeps

    closed = run_sweeps()
    if any(closed.values()):
        log.info("sweep: closed %s", closed)
    return closed
