"""
toto.metering.utils
===================

Best-effort instrumentation helper for source apps.

Pattern for source apps:

    from toto.metering.utils import safe_record_usage
    from .models import VaultUsageEvent, VaultUsageQuota

    safe_record_usage(
        event_model=VaultUsageEvent,
        quota_model=VaultUsageQuota,   # optional
        metric_code="storage.transfer_mb",
        quantity=size_mb,
        unit="MB",
        ...
        idempotency_key=f"vault.upload.transfer:{file.pk}",
        enforce_quota=False,           # non-blocking by default
    )

If event_model is not provided the call is a no-op (returns None).
If metering is not installed the call is a no-op (returns None).
All other errors are swallowed and logged — source operations are never broken.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("metering")


def metering_enabled() -> bool:
    from django.apps import apps
    return apps.is_installed("toto.metering")


def safe_record_usage(*, event_model=None, **kwargs) -> object | None:
    """
    Record a usage event, swallowing all errors.

    Returns the UsageEvent on success, None on failure / missing event_model /
    metering disabled.

    enforce_quota defaults to False (non-blocking).
    """
    if not metering_enabled():
        return None
    if event_model is None:
        # Source app hasn't provided its own event model yet — silent no-op.
        return None
    kwargs.setdefault("enforce_quota", False)
    try:
        from toto.metering.services import record_usage
        return record_usage(event_model=event_model, **kwargs)
    except Exception:
        logger.exception(
            "Usage metering failed (metric=%s, event_model=%s)",
            kwargs.get("metric_code"),
            event_model,
        )
        return None
