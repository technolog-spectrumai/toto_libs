"""The hourly refill. A thin wrapper: idempotency lives in the service.

The module name is a contract — autodiscovery imports ``<label>.tasks`` — and
the label is listed in ``toto.registry.TASK_MODULES``; ``toto.tests_schedules``
fails if the two drift apart.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("toto.mana")


@shared_task(name="toto.mana.tasks.regenerate_hour",
             soft_time_limit=1500, time_limit=1800)
def regenerate_hour() -> dict:
    """Refill every active member for the hour this fires in — from the
    CLOCK, never an argument, so a late beat pays its own hour and a worker
    back from a day away does not pay a day of refills at once."""
    from .services import regenerate_hour as run

    report = run()
    logger.info("mana: %s", report)
    return {"label": report.label, "paid": report.paid, "full": report.full,
            "skipped": report.skipped, "failed": report.failed,
            "failures": report.failures[:20]}
