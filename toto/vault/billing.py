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

# Stable metric codes — match BillingMetric.code rows seeded by ingress_tariffs.
# Vault passes these as strings to tariff services; the tariffs layer resolves
# them to first-class BillingMetric objects via metric__code= lookups.
STORAGE_REQUEST_METRIC_CODE  = "storage.request"
STORAGE_TRANSFER_METRIC_CODE = "storage.transfer_mb"
STORAGE_HOUR_METRIC_CODE     = "storage.mb_hour"

# Unit codes must match BillingUnit.code values seeded by ingress_tariffs.
UNIT_REQUEST = "request"
UNIT_MB      = "storage.mb"
UNIT_MB_HOUR = "storage.mb_hour"


def _get_tariff(bucket=None):
    from toto.tariffs.models import Tariff, TariffStatus
    if bucket is not None and bucket.tariff_id:
        t = bucket.tariff
        if t.status == TariffStatus.ACTIVE:
            return t
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


def preflight_upload_check(user, bucket, size_bytes: int) -> tuple[bool, str]:
    """
    Read-only check: can this user afford the upload charges for a file of
    the given size?  Call this BEFORE saving the VaultFile.

    Returns (True, "") if affordable or if no tariff/items are configured.
    Returns (False, message) if the user lacks sufficient balance or has no
    billing account set up.
    """
    tariff = _get_tariff(bucket=bucket)
    if not tariff:
        return True, ""

    from toto.assets.models import LedgerAccount
    from toto.tariffs.services import check_can_afford

    code = f"vault-user-{user.pk}"
    try:
        payer = LedgerAccount.objects.get(code=code)
    except LedgerAccount.DoesNotExist:
        return False, "No billing account found for this user. Please contact support."

    size_mb = Decimal(str(size_bytes)) / Decimal("1048576")

    charges = [(STORAGE_REQUEST_METRIC_CODE, Decimal("1"), UNIT_REQUEST)]
    if size_mb > 0:
        charges.append((STORAGE_TRANSFER_METRIC_CODE, size_mb, UNIT_MB))

    return check_can_afford(tariff, payer, charges)


def charge_upload(vault_file) -> list:
    """
    Post upload charges for a newly created VaultFile.
    Called from the post_save signal (created=True).
    Returns list of posted UsageRecords; empty list on any failure.
    """
    tariff = _get_tariff(bucket=vault_file.bucket)
    if not tariff:
        return []

    from toto.tariffs.services import record_and_post_usage

    payer = get_or_create_payer_account(vault_file.owner)
    size_bytes = vault_file.file_size_bytes or 0
    size_mb = Decimal(str(size_bytes)) / Decimal("1048576")
    results = []

    # Charge 1: upload request event
    try:
        record, _ = record_and_post_usage(
            tariff=tariff,
            payer_account=payer,
            metric_code=STORAGE_REQUEST_METRIC_CODE,
            quantity=Decimal("1"),
            unit=UNIT_REQUEST,
            source_type="vault_file",
            source_id=str(vault_file.pk),
            metadata={
                "vault_file_id": vault_file.pk,
                "title": vault_file.title,
                "bucket_slug": vault_file.bucket.slug if vault_file.bucket else None,
                "bucket_id": vault_file.bucket.pk if vault_file.bucket else None,
            },
        )
        results.append(record)
    except ValueError as exc:
        logger.warning("vault billing: upload request charge failed for file %s: %s", vault_file.pk, exc)

    # Charge 2: data transfer (MB)
    if size_mb > 0:
        try:
            record, _ = record_and_post_usage(
                tariff=tariff,
                payer_account=payer,
                metric_code=STORAGE_TRANSFER_METRIC_CODE,
                quantity=size_mb,
                unit=UNIT_MB,
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


def charge_storage_snapshot(
    user,
    quota_mb: Decimal | None = None,
    bucket=None,
    bucketless_only: bool = False,
) -> object | None:
    """
    Post one mb_hour usage record for the user's current total vault storage.
    Intended to be called once per billing tick (hourly / daily / etc.).

    quota_mb        — optional cap: bill at most this many MB so users don't overpay.
    bucket          — if given, bills only files in this bucket using its tariff.
    bucketless_only — if True, bills only files with no bucket (bucket IS NULL).

    Returns the UsageRecord if posted, None if skipped.
    """
    from django.db.models import Sum
    from toto.vault.models import VaultFile

    tariff = _get_tariff(bucket=bucket)
    if not tariff:
        return None

    qs = VaultFile.objects.filter(owner=user)
    if bucket is not None:
        qs = qs.filter(bucket=bucket)
    elif bucketless_only:
        qs = qs.filter(bucket__isnull=True)
    total_bytes = qs.aggregate(total=Sum("file_size_bytes"))["total"] or 0
    if total_bytes <= 0:
        return None

    from toto.tariffs.services import record_and_post_usage

    total_mb = Decimal(str(total_bytes)) / Decimal("1048576")
    if quota_mb is not None:
        total_mb = min(total_mb, Decimal(str(quota_mb)))

    payer = get_or_create_payer_account(user)

    try:
        record, _ = record_and_post_usage(
            tariff=tariff,
            payer_account=payer,
            metric_code=STORAGE_HOUR_METRIC_CODE,
            quantity=total_mb,
            unit=UNIT_MB_HOUR,
            source_type="vault_storage_snapshot",
            source_id=f"user-{user.pk}" if bucket is None else f"user-{user.pk}-bucket-{bucket.pk}",
            metadata={
                "user_id": user.pk,
                "username": user.username,
                "total_bytes": total_bytes,
                "total_mb": str(total_mb),
                **({"quota_mb": str(quota_mb)} if quota_mb is not None else {}),
                **({"bucket_id": bucket.pk, "bucket_slug": bucket.slug} if bucket is not None else {}),
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
