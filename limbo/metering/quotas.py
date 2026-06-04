"""
toto.metering.quotas
====================

Quota window calculation, usage aggregation, and quota evaluation.

All functions accept model classes as arguments — metering owns no tables.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.db.models import Q, Sum
from django.utils import timezone

from .models import QuotaMode, QuotaPeriod
from .primitives import QuotaDecision


# ---------------------------------------------------------------------------
# quota_window
# ---------------------------------------------------------------------------

def quota_window(quota, at=None):
    """
    Return (period_start, period_end) for the quota window containing `at`.

    For lifetime: returns (quota.starts_at, quota.ends_at) — both may be None.
    For rolling: returns (at - rolling_seconds, at).
    For calendar periods: returns calendar boundaries, bounded by starts_at/ends_at.
    """
    at = at or timezone.now()
    p = quota.period

    if p == QuotaPeriod.LIFETIME:
        return quota.starts_at, quota.ends_at

    if p == QuotaPeriod.ROLLING:
        secs = quota.rolling_seconds or 86400
        start = at - timedelta(seconds=secs)
        end = at
    elif p == QuotaPeriod.DAILY:
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
    elif p == QuotaPeriod.WEEKLY:
        start = (at - timedelta(days=at.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = start + timedelta(weeks=1)
    elif p == QuotaPeriod.MONTHLY:
        start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(
            year=start.year + (1 if start.month == 12 else 0),
            month=1 if start.month == 12 else start.month + 1,
        )
    elif p == QuotaPeriod.YEARLY:
        start = at.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(year=start.year + 1)
    else:
        return None, None

    # Honour outer bounds declared on the quota
    if quota.starts_at and start < quota.starts_at:
        start = quota.starts_at
    if quota.ends_at and end > quota.ends_at:
        end = quota.ends_at

    return start, end


# ---------------------------------------------------------------------------
# usage_total_for_quota
# ---------------------------------------------------------------------------

def usage_total_for_quota(*, event_model, quota, at=None) -> Decimal:
    """
    Sum event_model.quantity for recorded events matching the quota's
    metric_code, subject, and time window.
    """
    from .models import EventStatus
    at = at or timezone.now()
    period_start, period_end = quota_window(quota, at)

    qs = event_model.objects.filter(
        metric_code=quota.metric_code,
        status=EventStatus.RECORDED,
    )

    if quota.subject_type and quota.subject_id:
        qs = qs.filter(subject_type=quota.subject_type, subject_id=quota.subject_id)

    if period_start:
        qs = qs.filter(occurred_at__gte=period_start)
    if period_end:
        qs = qs.filter(occurred_at__lt=period_end)

    return qs.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


# ---------------------------------------------------------------------------
# matching_quotas
# ---------------------------------------------------------------------------

def matching_quotas(*, quota_model, metric_code, subject_type="", subject_id="", at=None):
    """
    Return active quotas for metric_code that cover `at` and match the subject
    (global quotas always match; subject-specific quotas match only on exact
    subject_type + subject_id).
    """
    at = at or timezone.now()
    qs = (
        quota_model.objects
        .filter(metric_code=metric_code, is_active=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=at))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=at))
        .filter(
            Q(subject_type="", subject_id="") |
            Q(subject_type=subject_type, subject_id=subject_id)
        )
    )
    return qs


# ---------------------------------------------------------------------------
# evaluate_quota
# ---------------------------------------------------------------------------

def evaluate_quota(
    *,
    event_model,
    quota_model,
    metric_code: str,
    quantity,
    subject_type: str = "",
    subject_id: str = "",
    at=None,
) -> QuotaDecision:
    """
    Evaluate all matching quotas and return the strictest QuotaDecision.

    - BLOCK quota exceeded → allowed=False
    - WARN quota exceeded → allowed=True with reason
    - TRACK quota → never blocks
    - No quotas → allowed=True, empty quota fields
    """
    at = at or timezone.now()
    quantity = Decimal(str(quantity))

    block_decision: Optional[QuotaDecision] = None
    warn_decision: Optional[QuotaDecision] = None

    for quota in matching_quotas(
        quota_model=quota_model,
        metric_code=metric_code,
        subject_type=subject_type,
        subject_id=subject_id,
        at=at,
    ):
        used = usage_total_for_quota(event_model=event_model, quota=quota, at=at)
        period_start, period_end = quota_window(quota, at)
        remaining = quota.limit_quantity - used
        will_exceed = (used + quantity) > quota.limit_quantity

        d = QuotaDecision(
            allowed=True,
            mode=quota.mode,
            quota_id=quota.pk,
            quota_code=quota.code,
            used_quantity=used,
            requested_quantity=quantity,
            limit_quantity=quota.limit_quantity,
            remaining_quantity=max(Decimal("0"), remaining),
            period_start=period_start,
            period_end=period_end,
            reason=(
                f"Quota '{quota.code}' would be exceeded "
                f"(used={used}, requested={quantity}, limit={quota.limit_quantity})"
            ) if will_exceed else "",
        )

        if will_exceed:
            if quota.mode == QuotaMode.BLOCK:
                d.allowed = False
                if block_decision is None:
                    block_decision = d
            elif quota.mode == QuotaMode.WARN and warn_decision is None:
                warn_decision = d

    if block_decision:
        return block_decision
    if warn_decision:
        return warn_decision

    return QuotaDecision(
        allowed=True,
        mode="track",
        quota_id=None,
        quota_code="",
        used_quantity=Decimal("0"),
        requested_quantity=quantity,
        limit_quantity=Decimal("0"),
        remaining_quantity=Decimal("0"),
        period_start=None,
        period_end=None,
        reason="",
    )
