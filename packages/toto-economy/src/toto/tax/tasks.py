"""Celery tasks for the levy engine.

The module name is a hard contract: autodiscovery imports ``<label>.tasks``
and nothing else, and the label must also appear in ``toto.registry.
TASK_MODULES``. The task is a thin wrapper over a plain service function, so a
host with no worker can call the service directly. Idempotency lives in the
service (per-user, per-day usage-event keys), which is what makes a double
fire — or a manual run beside the beat — free.
"""

from __future__ import annotations

import logging

from celery import shared_task

from . import services

logger = logging.getLogger("toto.tax")


@shared_task(name="toto.tax.tasks.run_daily_levy",
             soft_time_limit=3300, time_limit=3600)
def run_daily_levy() -> list[dict]:
    summaries = services.run_daily_levy()
    return [
        {"metric_code": s.metric_code, "day": s.day,
         "skipped_reason": s.skipped_reason, "counts": s.counts}
        for s in summaries
    ]
