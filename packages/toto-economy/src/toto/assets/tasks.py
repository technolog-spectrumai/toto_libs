"""Celery tasks for the ledger.

The module name is a hard contract: autodiscovery imports ``<label>.tasks`` and
nothing else, and the label must also appear in ``toto.registry.TASK_MODULES``.

The task is a thin wrapper over a plain service function, so a host with no
worker can run an hour by hand. **Idempotency lives in the service**, not here
and not in the beat — ``FaucetPayout`` carries a unique
``(member, period_label)`` and every payout's ledger reference is unique too, so
a double fire, an overlapping worker and a manual run beside the beat are all
free. That is what makes retrying this task safe, which is the only reason it
can be retried at all.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger("toto.assets.faucets")


@shared_task(name="toto.assets.tasks.run_faucet_hour",
             soft_time_limit=1500, time_limit=1800)
def run_faucet_hour() -> dict:
    """Pay every due faucet member for the hour this fires in.

    The hour comes from the CLOCK, not from an argument, so a beat that fires
    late pays the hour it is actually in rather than silently backfilling one
    nobody asked for. A missed hour stays missed — the same choice the daily
    levy makes, and for the same reason: a sweep that catches up on its own can
    pay a month of arrears the moment a worker comes back.
    """
    from .services import faucets

    report = faucets.run_hour()
    logger.info("faucets: %s", report)
    return {
        "label": report.label,
        "paid": report.paid,
        "skipped": report.skipped,
        "failed": report.failed,
        "failures": report.failures[:20],
    }
