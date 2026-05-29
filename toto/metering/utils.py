"""
Shared instrumentation helpers for source apps.

Usage in source apps:

    from toto.metering.utils import safe_record_usage

All calls are best-effort: if metering is unavailable or raises, the
source operation continues normally.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("metering")

# Whether to enforce quotas (can be overridden per call or via settings).
# Default is False — non-blocking instrumentation.
ENFORCE_QUOTAS_DEFAULT = False


def metering_enabled() -> bool:
    from django.apps import apps
    return apps.is_installed("toto.metering")


def safe_record_usage(**kwargs) -> object | None:
    """
    Record a usage event, swallowing all errors so source ops are never broken.

    Returns the UsageEvent on success, None on failure or when metering is off.
    Quota enforcement defaults to ENFORCE_QUOTAS_DEFAULT (False).
    """
    if not metering_enabled():
        return None
    kwargs.setdefault("enforce_quota", ENFORCE_QUOTAS_DEFAULT)
    try:
        from toto.metering.services import record_usage
        return record_usage(**kwargs)
    except Exception:
        logger.exception("Usage metering failed (metric=%s)", kwargs.get("metric_code"))
        return None
