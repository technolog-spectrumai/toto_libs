import logging

from celery import shared_task
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from toto.assets.models import from_base_units
from toto.assets.services.assets import transfer_asset

logger = logging.getLogger(__name__)

DAILY_ALLOWANCE_TYPES = ("per_diem", "meal")


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def pay_daily_allowances(self):
    """Transfer all active per-diem and meal allowances for today.

    Runs at end of business day (Mon–Fri 17:00). Safe to retry — each
    payout uses reference 'allowance-<pk>-<date>' which is unique per day,
    so a duplicate run simply finds the reference already exists and skips.
    """
    from toto.assets.models import LedgerTransaction
    from toto.kanban.models import PractitionerAllowance

    today = timezone.now().date()

    if today.weekday() >= 5:
        return {"skipped": "weekend", "date": today.isoformat()}

    date_str = today.isoformat()

    allowances = (
        PractitionerAllowance.objects
        .filter(
            active=True,
            allowance_type__in=DAILY_ALLOWANCE_TYPES,
            recipient_account__isnull=False,
        )
        .filter(Q(valid_from__isnull=True) | Q(valid_from__lte=today))
        .filter(Q(valid_until__isnull=True) | Q(valid_until__gte=today))
        .select_related(
            "practitioner__person",
            "payer_account",
            "recipient_account",
            "asset",
        )
    )

    paid = skipped = failed = 0
    already_paid_refs = set(
        LedgerTransaction.objects
        .filter(reference__startswith="allowance-", metadata__date=date_str)
        .values_list("reference", flat=True)
    )

    for allowance in allowances:
        reference = f"allowance-{allowance.pk}-{date_str}"

        if reference in already_paid_refs:
            skipped += 1
            continue

        try:
            with transaction.atomic():
                transfer_asset(
                    asset=allowance.asset,
                    sender_account=allowance.payer_account,
                    receiver_account=allowance.recipient_account,
                    amount=from_base_units(allowance.amount_base_units, allowance.asset.decimals),
                    reference=reference,
                    description=(
                        f"{allowance.get_allowance_type_display()} — "
                        f"{allowance.practitioner} — {date_str}"
                    ),
                    metadata={
                        "source": "allowance_payout",
                        "allowance_id": allowance.pk,
                        "practitioner_id": allowance.practitioner_id,
                        "date": date_str,
                    },
                )
            paid += 1
            logger.info("Paid allowance %s for %s on %s", allowance.pk, allowance.practitioner, date_str)
        except Exception as exc:
            failed += 1
            logger.error(
                "Failed to pay allowance %s for %s: %s",
                allowance.pk, allowance.practitioner, exc,
            )

    logger.info(
        "pay_daily_allowances %s: paid=%d skipped=%d failed=%d",
        date_str, paid, skipped, failed,
    )
    return {"paid": paid, "skipped": skipped, "failed": failed, "date": date_str}
