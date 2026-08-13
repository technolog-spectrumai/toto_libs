"""The monthly sweep.

One task, daily rather than monthly: a monthly beat that misses its day misses
a month, and `materialize` is idempotent by period label so running it every day
costs one query per subscription and creates nothing new. The same reasoning as
the levy's nightly run.

`toto.subscriptions` MUST appear in `TASK_MODULES` in `toto/registry.py`, or
celery's autodiscovery never imports this file and the beat entry below raises
`KeyError` twice a day with nobody watching. That is not hypothetical — it is
exactly what happened to `toto.weather`.
"""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="toto.subscriptions.tasks.run_billing", soft_time_limit=1800)
def run_billing():
    from . import services

    counts = services.run_billing()
    logger.info("subscriptions: %s", counts)
    return counts
