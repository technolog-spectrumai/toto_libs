"""toto.core's one task: the nightly housekeeping (2026-10-01).

The module name is a hard contract: autodiscovery imports ``<label>.tasks``
and nothing else, and "toto.core" must stay in ``toto.registry.TASK_MODULES``.
The work is ``housekeeping.run``; this is only its door onto a worker, put
there by the beat (``toto.schedules``, ``housekeeping=True``).
"""

from celery import shared_task

TASK_NAME = "toto.core.tasks.nightly_housekeeping"


@shared_task(name=TASK_NAME, soft_time_limit=1500)
def nightly_housekeeping() -> dict:
    """Expired sessions, the sign-in rows of sessions that are gone, and
    membership applications that lapsed long enough ago — one audit record
    per run, counts only. Idempotent: a second fire finds nothing due."""
    from .housekeeping import run

    return run(source="beat")
