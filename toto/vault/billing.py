"""
Vault storage billing — integrates with toto.tariffs.

Two charge types:
  1. Upload event  — storage.request (× 1) + storage.transfer_mb (× MB uploaded)
  2. Storage snapshot — storage.mb_hour (× total MB stored) called once per billing tick

Payer accounts are created on demand, keyed by vault-user-{user.pk}.
All billing failures are swallowed: uploads never block on billing errors.
"""
from __future__ import annotations

import logging
from decimal import Decimal

logger = logging.getLogger(__name__)

STORAGE_TARIFF_CODE = "FILE-STORAGE"
UPLOAD_REQUEST_METRIC = "storage.request"
UPLOAD_TRANSFER_METRIC = "storage.transfer_mb"
STORAGE_HOUR_METRIC = "storage.mb_hour"


def _get_tariff():
    from toto.tariffs.models import Tariff, TariffStatus
    return Tariff.objects.filter(code=STORAGE_TARIFF_CODE, status=TariffStatus.ACTIVE).first()


def get_or_create_payer_account(user):
    from toto.assets.models import AccountType, LedgerAccount
    code = f"vault-user-{user.pk}"
    account, _ = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={
            "name": user.get_full_name() or user.username,
            "account_type": AccountType.USER,
            "active": True,
        },
    )
    return account


def _bu(slug):
    from toto.tariffs.models import BillingUnit
    return BillingUnit.objects.filter(slug=slug).first()


def charge_upload(vault_file) -> list:
    """
    Post upload charges for a newly created VaultFile.
    Called from the post_save signal (created=True).
    Returns list of posted UsageRecords; empty list on any failure.
    """
    tariff = _get_tariff()
    if not tariff:
        return []

    from toto.tariffs.services import record_and_post_usage

    payer = get_or_create_payer_account(vault_file.owner)
    size_bytes = vault_file.file_size_bytes or 0
    size_mb = Decimal(str(size_bytes)) / Decimal("1048576")
    results = []

    # Charge 1: upload request event
    unit_request = _bu("request")
    if unit_request:
        try:
            record, _ = record_and_post_usage(
                tariff=tariff,
                payer_account=payer,
                metric_code=UPLOAD_REQUEST_METRIC,
                quantity=Decimal("1"),
                unit=unit_request,
                source_type="vault_file",
                source_id=str(vault_file.pk),
                metadata={
                    "vault_file_id": vault_file.pk,
                    "title": vault_file.title,
                    "bucket": vault_file.bucket.slug if vault_file.bucket else None,
                },
            )
            results.append(record)
        except ValueError as exc:
            logger.warning("vault billing: upload request charge failed for file %s: %s", vault_file.pk, exc)

    # Charge 2: data transfer (MB)
    unit_mb = _bu("mb")
    if unit_mb and size_mb > 0:
        try:
            record, _ = record_and_post_usage(
                tariff=tariff,
                payer_account=payer,
                metric_code=UPLOAD_TRANSFER_METRIC,
                quantity=size_mb,
                unit=unit_mb,
                source_type="vault_file",
                source_id=str(vault_file.pk),
                metadata={
                    "vault_file_id": vault_file.pk,
                    "size_bytes": size_bytes,
                },
            )
            results.append(record)
        except ValueError as exc:
            logger.warning("vault billing: transfer charge failed for file %s: %s", vault_file.pk, exc)

    return results


def charge_storage_snapshot(user) -> object | None:
    """
    Post one mb_hour usage record for the user's current total vault storage.
    Intended to be called once per billing tick (hourly / daily / etc.).
    Returns the UsageRecord if posted, None if skipped.
    """
    from django.db.models import Sum
    from toto.vault.models import VaultFile

    tariff = _get_tariff()
    if not tariff:
        return None

    total_bytes = (
        VaultFile.objects.filter(owner=user)
        .aggregate(total=Sum("file_size_bytes"))["total"] or 0
    )
    if total_bytes <= 0:
        return None

    unit_mb_hour = _bu("mb_hour")
    if not unit_mb_hour:
        return None

    from toto.tariffs.services import record_and_post_usage

    total_mb = Decimal(str(total_bytes)) / Decimal("1048576")
    payer = get_or_create_payer_account(user)

    try:
        record, _ = record_and_post_usage(
            tariff=tariff,
            payer_account=payer,
            metric_code=STORAGE_HOUR_METRIC,
            quantity=total_mb,
            unit=unit_mb_hour,
            source_type="vault_storage_snapshot",
            source_id=f"user-{user.pk}",
            metadata={
                "user_id": user.pk,
                "username": user.username,
                "total_bytes": total_bytes,
                "total_mb": str(total_mb),
            },
        )
        return record
    except ValueError as exc:
        logger.warning("vault billing: storage snapshot failed for user %s: %s", user.pk, exc)
        return None


def get_user_storage_summary(user) -> dict:
    """Return current storage stats for a user (no DB writes)."""
    from django.db.models import Count, Sum
    from toto.vault.models import VaultFile

    qs = VaultFile.objects.filter(owner=user)
    agg = qs.aggregate(total_bytes=Sum("file_size_bytes"), file_count=Count("id"))
    total_bytes = agg["total_bytes"] or 0
    return {
        "file_count": agg["file_count"] or 0,
        "total_bytes": total_bytes,
        "total_mb": round(total_bytes / 1_048_576, 4),
        "total_gb": round(total_bytes / 1_073_741_824, 6),
    }
