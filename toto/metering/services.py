"""
Metering services.

Pure orchestration: records usage facts and evaluates quotas.
No pricing, no assets, no tariffs, no invoices, no ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db.models import Q, Sum
from django.utils import timezone


# ---------------------------------------------------------------------------
# QuotaDecision dataclass + QuotaExceeded exception
# ---------------------------------------------------------------------------

@dataclass
class QuotaDecision:
    allowed: bool
    mode: str
    quota_id: Optional[int]
    quota_code: str
    used_quantity: Decimal
    requested_quantity: Decimal
    limit_quantity: Decimal
    remaining_quantity: Decimal
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    reason: str


class QuotaExceeded(ValidationError):
    def __init__(self, decision: QuotaDecision):
        self.decision = decision
        super().__init__(
            f"Quota '{decision.quota_code}' exceeded: "
            f"used {decision.used_quantity}, limit {decision.limit_quantity}."
        )


# ---------------------------------------------------------------------------
# quota_window
# ---------------------------------------------------------------------------

def quota_window(quota, at=None):
    """Return (period_start, period_end) for the quota window containing `at`."""
    from .models import QuotaPeriod
    at = at or timezone.now()
    p = quota.period

    if p == QuotaPeriod.LIFETIME:
        return quota.starts_at, quota.ends_at

    if p == QuotaPeriod.ROLLING:
        secs = quota.rolling_seconds or 86400
        return at - timedelta(seconds=secs), at

    if p == QuotaPeriod.DAILY:
        start = at.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1)

    if p == QuotaPeriod.WEEKLY:
        start = (at - timedelta(days=at.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return start, start + timedelta(weeks=1)

    if p == QuotaPeriod.MONTHLY:
        start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = start.replace(
            year=start.year + (1 if start.month == 12 else 0),
            month=1 if start.month == 12 else start.month + 1,
        )
        return start, end

    if p == QuotaPeriod.YEARLY:
        start = at.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, start.replace(year=start.year + 1)

    return None, None


# ---------------------------------------------------------------------------
# usage_total_for_quota
# ---------------------------------------------------------------------------

def usage_total_for_quota(quota, at=None):
    """Sum recorded UsageEvent.quantity for the quota's window and subject."""
    from .models import UsageEvent, EventStatus
    at = at or timezone.now()
    period_start, period_end = quota_window(quota, at)

    qs = UsageEvent.objects.filter(metric=quota.metric, status=EventStatus.RECORDED)

    if quota.subject_type and quota.subject_id:
        qs = qs.filter(subject_type=quota.subject_type, subject_id=quota.subject_id)

    if period_start:
        qs = qs.filter(occurred_at__gte=period_start)
    if period_end:
        qs = qs.filter(occurred_at__lt=period_end)

    return qs.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


# ---------------------------------------------------------------------------
# evaluate_quota
# ---------------------------------------------------------------------------

def evaluate_quota(metric, quantity, subject_type="", subject_id="", at=None):
    """
    Check all active quotas for this metric/subject at time `at`.

    Returns a QuotaDecision. If any BLOCK quota would be exceeded,
    allowed=False. WARN quotas return allowed=True with a reason.
    TRACK quotas never block.
    """
    from .models import UsageQuota, QuotaMode
    at = at or timezone.now()
    quantity = Decimal(str(quantity))

    active = (
        UsageQuota.objects.filter(metric=metric, is_active=True)
        .filter(Q(starts_at__isnull=True) | Q(starts_at__lte=at))
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=at))
        .filter(
            Q(subject_type="", subject_id="")
            | Q(subject_type=subject_type, subject_id=subject_id)
        )
    )

    block_decision = None
    warn_decision = None

    for quota in active:
        used = usage_total_for_quota(quota, at)
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
                if will_exceed else ""
            ),
        )

        if will_exceed:
            if quota.mode == QuotaMode.BLOCK:
                d.allowed = False
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


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------

def record_usage(
    *,
    metric_code: str,
    quantity,
    unit: str = "",
    occurred_at=None,
    source_type: str = "",
    source_id: str = "",
    source_label: str = "",
    subject_type: str = "",
    subject_id: str = "",
    subject_label: str = "",
    idempotency_key: str = "",
    description: str = "",
    metadata=None,
    enforce_quota: bool = True,
    create_metric: bool = False,
):
    """
    Create a UsageEvent.

    - Resolves UsageMetric by code (creates if create_metric=True).
    - Idempotency: returns existing event when idempotency_key matches.
    - Evaluates quotas when enforce_quota=True; raises QuotaExceeded on BLOCK.
    - No pricing, no assets, no tariffs, no invoices.
    """
    from .models import UsageMetric, UsageEvent
    quantity = Decimal(str(quantity))

    try:
        metric = UsageMetric.objects.get(code=metric_code)
    except UsageMetric.DoesNotExist:
        if create_metric:
            metric = UsageMetric.objects.create(code=metric_code, name=metric_code)
        else:
            raise ValidationError(f"UsageMetric '{metric_code}' not found.")

    if idempotency_key:
        existing = UsageEvent.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    if enforce_quota:
        decision = evaluate_quota(
            metric, quantity,
            subject_type=subject_type,
            subject_id=subject_id,
            at=occurred_at,
        )
        if not decision.allowed:
            raise QuotaExceeded(decision)

    return UsageEvent.objects.create(
        metric=metric,
        quantity=quantity,
        unit=unit or metric.default_unit,
        occurred_at=occurred_at or timezone.now(),
        source_type=source_type,
        source_id=source_id,
        source_label=source_label,
        subject_type=subject_type,
        subject_id=subject_id,
        subject_label=subject_label,
        idempotency_key=idempotency_key,
        description=description,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# void_usage_event
# ---------------------------------------------------------------------------

def void_usage_event(event, reason: str = "", actor=None):
    """Mark a UsageEvent as voided. Does not delete."""
    from .models import EventStatus
    event.status = EventStatus.VOIDED
    meta = dict(event.metadata or {})
    if reason:
        meta["void_reason"] = reason
    if actor is not None:
        meta["voided_by"] = str(actor)
    event.metadata = meta
    event.save(update_fields=["status", "metadata", "updated_at"])
    return event


# ---------------------------------------------------------------------------
# metering_metrics
# ---------------------------------------------------------------------------

def metering_metrics(at=None):
    """Aggregate statistics for the metering dashboard."""
    from datetime import date
    from .models import UsageEvent, UsageQuota, EventStatus, QuotaMode
    from django.db.models import Count
    from django.db.models.functions import TruncDate

    at = at or timezone.now()

    events = UsageEvent.objects.all()
    recorded = events.filter(status=EventStatus.RECORDED)

    by_status = list(
        events.values("status").annotate(count=Count("id")).order_by("status")
    )
    by_metric = list(
        events.values("metric__code", "metric__namespace")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    totals_by_metric = list(
        recorded.values("metric__code", "unit")
        .annotate(total=Sum("quantity"))
        .order_by("metric__code")
    )
    totals_by_namespace = list(
        recorded.values("metric__namespace")
        .annotate(total=Sum("quantity"), count=Count("id"))
        .order_by("metric__namespace")
    )
    totals_by_source = list(
        recorded.exclude(source_type="")
        .values("source_type")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )
    totals_by_subject = list(
        recorded.exclude(subject_type="")
        .values("subject_type")
        .annotate(count=Count("id"))
        .order_by("-count")[:20]
    )

    thirty_ago = at - timedelta(days=29)
    daily_map = {
        e["day"]: e["count"]
        for e in events.filter(occurred_at__gte=thirty_ago)
        .annotate(day=TruncDate("occurred_at"))
        .values("day")
        .annotate(count=Count("id"))
    }
    today = at.date()
    daily_series = [
        {
            "date": (today - timedelta(days=29 - i)).strftime("%m-%d"),
            "count": daily_map.get(today - timedelta(days=29 - i), 0),
        }
        for i in range(30)
    ]

    active_quota_count = (
        UsageQuota.objects.filter(is_active=True)
        .filter(Q(ends_at__isnull=True) | Q(ends_at__gt=at))
        .count()
    )

    quotas_near_limit = []
    warn_exceeded = []
    for quota in UsageQuota.objects.filter(is_active=True).select_related("metric")[:100]:
        if quota.limit_quantity <= 0:
            continue
        used = usage_total_for_quota(quota, at)
        pct = float(used / quota.limit_quantity * 100)
        entry = {"quota": quota, "used": used, "pct": round(pct, 1)}
        if pct >= 80:
            quotas_near_limit.append(entry)
        if pct > 100 and quota.mode == QuotaMode.WARN:
            warn_exceeded.append(entry)

    return {
        "by_status": by_status,
        "by_metric": by_metric,
        "totals_by_metric": totals_by_metric,
        "totals_by_namespace": totals_by_namespace,
        "totals_by_source": totals_by_source,
        "totals_by_subject": totals_by_subject,
        "daily_series": daily_series,
        "active_quota_count": active_quota_count,
        "quotas_near_limit": sorted(quotas_near_limit, key=lambda x: -x["pct"])[:10],
        "warn_exceeded": warn_exceeded[:10],
    }
