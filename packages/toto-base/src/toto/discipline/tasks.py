"""The hourly regeneration. A thin wrapper: idempotency lives in the service.

The module name is a contract: autodiscovery imports ``<label>.tasks``, and
the label is listed in ``toto.registry.TASK_MODULES``; ``toto.tests_schedules``
fails if the two drift apart.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("toto.discipline")


@shared_task(name="toto.discipline.tasks.regenerate_hour",
             soft_time_limit=1500, time_limit=1800)
def regenerate_hour() -> dict:
    """Regenerate every active person for the hour this fires in: from the
    clock, never an argument, so a worker back from a day away does not pay
    a day at once."""
    from .services import regenerate_hour as run

    report = run()
    logger.info("discipline: %s", report)
    return {"label": report.label, "paid": report.paid, "full": report.full,
            "idle": report.idle, "done": report.done, "total": str(report.total)}
