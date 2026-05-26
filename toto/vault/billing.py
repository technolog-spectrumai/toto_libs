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


def _resolve_token_name(tariff) -> str:
    """Look up the primary token name for a tariff's storage items."""
    from toto.tariffs.models import TariffItem
    item = (
        TariffItem.objects
        .filter(tariff=tariff, active=True, metric__code=STORAGE_REQUEST_METRIC_CODE)
        .select_related("charged_asset")
        .first()
    )
    if item:
        return item.charged_asset.name
    return "upload"


def get_payer_account(user):
    """
    Returns the LedgerAccount linked via the user's StorageAccount.
    Returns None if no StorageAccount exists or it is inactive.
    """
    from toto.vault.models import StorageAccount
    try:
        sa = StorageAccount.objects.select_related("ledger_account").get(user=user, active=True)
        return sa.ledger_account
    except StorageAccount.DoesNotExist:
        return None


def get_or_create_payer_account(user):
    """Legacy helper used by charge_upload / charge_storage_snapshot.
    Prefers StorageAccount.ledger_account; falls back to creating one via magic code."""
    account = get_payer_account(user)
    if account:
        return account
    from toto.assets.models import AccountType, LedgerAccount
    code = f"vault-user-{user.pk}"
    account, created = LedgerAccount.objects.get_or_create(
        code=code,
        defaults={
            "name": user.get_full_name() or user.username,
            "account_type": AccountType.USER,
            "active": True,
            "user": user,
        },
    )
    if not created and account.user_id != user.pk:
        account.user = user
        account.save(update_fields=["user", "updated_at"])
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

    from toto.tariffs.services import check_can_afford

    token_name = _resolve_token_name(tariff)

    payer = get_payer_account(user)
    if payer is None:
        return False, (
            f"Uploading to this bucket uses {token_name} credits. "
            "Connect a storage account in your vault settings to enable uploads."
        )

    size_mb = Decimal(str(size_bytes)) / Decimal("1048576")
    charges = [(STORAGE_REQUEST_METRIC_CODE, Decimal("1"), UNIT_REQUEST)]
    if size_mb > 0:
        charges.append((STORAGE_TRANSFER_METRIC_CODE, size_mb, UNIT_MB))

    ok, msg = check_can_afford(tariff, payer, charges)
    if not ok:
        return False, f"Not enough {token_name} credits. {msg}"
    return True, ""


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
    signer = _make_wallet_auth_signer(vault_file.owner)

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
            pre_post_hook=signer,
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
                pre_post_hook=signer,
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
    signer = _make_wallet_auth_signer(user)

    try:
        record, _ = record_and_post_usage(
            tariff=tariff,
            payer_account=payer,
            metric_code=STORAGE_HOUR_METRIC_CODE,
            quantity=total_mb,
            unit=UNIT_MB_HOUR,
            source_type="vault_storage_snapshot",
            source_id=f"user-{user.pk}" if bucket is None else f"user-{user.pk}-bucket-{bucket.pk}",
            pre_post_hook=signer,
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


def _make_wallet_auth_signer(user):
    """
    Return a pre_post_hook that signs the billing transaction using the user's
    WalletAuthorization. Returns None if no active auto-authorization is configured.
    The hook is best-effort: signing errors are swallowed so uploads never block.
    """
    try:
        from toto.vault.models import StorageAccount
        sa = StorageAccount.objects.select_related("authorization").get(user=user, active=True)
    except Exception:
        return None

    auth = sa.authorization
    if not auth or not auth.active:
        return None

    def _sign_tx(tx):
        import base64
        import os
        import uuid as _uuid
        from django.utils import timezone
        from toto.assets.signing import build_transaction_payload, hash_payload, sign_payload

        pem = auth.get_private_key()
        if not pem:
            return
        nonce = base64.b64encode(os.urandom(16)).decode()
        idempotency_key = tx.idempotency_key or str(_uuid.uuid4())
        payload = build_transaction_payload(tx, nonce, idempotency_key)
        sig = sign_payload(payload, pem, "auto")
        tx.signature = sig
        tx.payload_hash = hash_payload(payload)
        tx.nonce = nonce
        tx.idempotency_key = idempotency_key
        tx.signed_at = timezone.now()

    return _sign_tx


def get_bucket_billing_summary(bucket, user=None) -> dict | None:
    """
    Returns billing display info for a bucket: token names, rates, user balance.
    Returns None if no tariff or no active items are configured.
    Called from views to populate billing context shown in public file list and gateway pages.
    """
    tariff = _get_tariff(bucket=bucket)
    if not tariff:
        return None

    from toto.tariffs.models import TariffItem

    items = list(
        TariffItem.objects
        .filter(tariff=tariff, active=True)
        .filter(metric__code__in=[STORAGE_REQUEST_METRIC_CODE, STORAGE_TRANSFER_METRIC_CODE])
        .select_related("metric", "charged_asset")
    )

    request_item = next((i for i in items if i.metric.code == STORAGE_REQUEST_METRIC_CODE), None)
    transfer_item = next((i for i in items if i.metric.code == STORAGE_TRANSFER_METRIC_CODE), None)
    primary_item = request_item or transfer_item
    if not primary_item:
        return None

    asset = primary_item.charged_asset
    result = {
        "tariff_name": tariff.name,
        "tariff_code": tariff.code,
        "token_name": asset.name,
        "token_unit": asset.unit_name,
        "request_rate": str(request_item.price_per_unit_display) if request_item else None,
        "request_asset": request_item.charged_asset.unit_name if request_item else None,
        "transfer_rate": str(transfer_item.price_per_unit_display) if transfer_item else None,
        "transfer_asset": transfer_item.charged_asset.unit_name if transfer_item else None,
        "user_balance": None,
        "user_balance_display": None,
        "has_billing_account": False,
    }

    if user is not None and getattr(user, "is_authenticated", False):
        from toto.assets.models import AssetHolding
        account = get_payer_account(user)
        if account:
            result["has_billing_account"] = True
            holding = AssetHolding.objects.filter(account=account, asset=asset).first()
            if holding:
                result["user_balance"] = str(holding.balance_display)
                result["user_balance_display"] = f"{holding.balance_display} {asset.unit_name}"
            else:
                result["user_balance"] = "0"
                result["user_balance_display"] = f"0 {asset.unit_name}"

    return result


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
