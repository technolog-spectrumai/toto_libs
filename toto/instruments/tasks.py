import logging

from celery import shared_task
from django.core.exceptions import ValidationError
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def process_due_subscriptions(self):
    """Bill all subscriptions whose next_billing_at has passed.

    Runs every hour. Safe to retry — SubscriptionService.bill() uses a
    per-period unique_together constraint so a duplicate call raises
    ValidationError("already paid") without double-charging.
    """
    from toto.instruments.models import SubscriptionContract, SubscriptionStatus
    from toto.instruments.services import SubscriptionService

    now = timezone.now()

    due = (
        SubscriptionContract.objects
        .filter(
            status__in=[SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.TRIALING],
            next_billing_at__lte=now,
        )
        .select_related(
            "instrument",
            "subscriber_account",
            "provider_account",
            "asset",
        )
    )

    billed = failed = skipped = 0

    for subscription in due:
        try:
            SubscriptionService.bill(subscription)
            billed += 1
            logger.info("Billed subscription %s", subscription.instrument.reference)
        except ValidationError as exc:
            msg = str(exc)
            if "already been paid" in msg:
                skipped += 1
            else:
                failed += 1
                logger.error("Failed to bill subscription %s: %s", subscription.instrument.reference, exc)
        except Exception as exc:
            failed += 1
            logger.error("Unexpected error billing subscription %s: %s", subscription.instrument.reference, exc)

    logger.info(
        "process_due_subscriptions: billed=%d skipped=%d failed=%d",
        billed, skipped, failed,
    )
    return {"billed": billed, "skipped": skipped, "failed": failed}
