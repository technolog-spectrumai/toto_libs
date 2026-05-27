from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def renew_due_subscriptions(self):
    """Renew all subscriptions whose current_period_end has passed.

    Runs every hour via Celery beat. The underlying service is idempotent —
    a subscription already renewed for the current period will be skipped.
    """
    from toto.subscriptions.services import renew_due_subscriptions as _service

    try:
        count = _service()
        logger.info("renew_due_subscriptions: renewed=%d", count)
        return {"renewed": count}
    except Exception as exc:
        logger.error("renew_due_subscriptions task error: %s", exc)
        raise self.retry(exc=exc)
