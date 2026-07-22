from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

logger = logging.getLogger("toto.quota")

_PERIOD_DELTAS = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
    "monthly": timedelta(days=30),
    "yearly": timedelta(days=365),
}


class QuotaExceeded(Exception):
    def __init__(self, policy, current: Decimal, limit: Decimal) -> None:
        self.policy = policy
        self.current = current
        self.limit = limit

    def __str__(self) -> str:
        p = self.policy
        return (
            f"{p.name or p.metric_code} quota exceeded "
            f"(used {self.current}/{self.limit} {p.unit or 'units'}"
            f"{' per ' + p.period if p.period != 'lifetime' else ''})"
        )


def get_policy(app_label: str, metric_code: str,
               subject_type: str = "", subject_id: str = ""):
    """
    Return the most-specific active QuotaPolicy for the given tuple.
    Subject-specific beats global (subject_type="" / subject_id="").
    Returns None if no active policy exists.
    """
    from .models import QuotaPolicy

    now = timezone.now()
    qs = QuotaPolicy.objects.filter(
        app_label=app_label,
        metric_code=metric_code,
        active=True,
    ).filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now)
    ).filter(
        Q(ends_at__isnull=True) | Q(ends_at__gt=now)
    )

    if subject_type and subject_id:
        specific = qs.filter(subject_type=subject_type, subject_id=subject_id).first()
        if specific:
            return specific

    return qs.filter(subject_type="", subject_id="").first()


def _period_start(period: str):
    """Return the start of the current period window, or None for lifetime."""
    if period == "lifetime":
        return None
    delta = _PERIOD_DELTAS.get(period, timedelta(days=1))
    return timezone.now() - delta


def _period_total(app_label: str, metric_code: str,
                  subject_type: str, subject_id: str,
                  since=None) -> Decimal:
    """Sum of recorded (non-voided) usage for the given subject+metric since `since`."""
    from .models import UsageEvent

    qs = UsageEvent.objects.filter(
        app_label=app_label,
        metric_code=metric_code,
        subject_type=subject_type,
        subject_id=subject_id,
        status="recorded",
    )
    if since is not None:
        qs = qs.filter(occurred_at__gte=since)

    result = qs.aggregate(total=Sum("quantity"))["total"]
    return result or Decimal("0")


def check_quota(app_label: str, metric_code: str, quantity,
                subject_type: str, subject_id: str) -> None:
    """
    Raise QuotaExceeded if an active block-mode policy would be exceeded.
    Does nothing if no policy exists or if mode != block.
    """
    policy = get_policy(app_label, metric_code, subject_type, subject_id)
    if policy is None or policy.mode != "block":
        return

    since = _period_start(policy.period)
    current = _period_total(app_label, metric_code, subject_type, subject_id, since)
    if current + Decimal(str(quantity)) > policy.limit:
        raise QuotaExceeded(policy, current, policy.limit)


def record_usage(
    app_label: str,
    metric_code: str,
    quantity,
    subject_type: str,
    subject_id: str,
    subject_label: str = "",
    source_type: str = "",
    source_id: str = "",
    source_label: str = "",
    idempotency_key: str = "",
    metadata: dict | None = None,
    occurred_at=None,
):
    """
    Best-effort usage recording. Never raises — all exceptions are logged.
    Returns the created UsageEvent or None.
    """
    from .models import UsageEvent

    try:
        if idempotency_key:
            if UsageEvent.objects.filter(idempotency_key=idempotency_key).exists():
                return None

        event = UsageEvent.objects.create(
            app_label=app_label,
            metric_code=metric_code,
            quantity=Decimal(str(quantity)),
            subject_type=subject_type,
            subject_id=subject_id,
            subject_label=subject_label,
            source_type=source_type,
            source_id=source_id,
            source_label=source_label,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
            occurred_at=occurred_at or timezone.now(),
        )
        return event
    except Exception:
        logger.exception(
            "quota: failed to record usage %s/%s qty=%s subject=%s:%s",
            app_label, metric_code, quantity, subject_type, subject_id,
        )
        return None


def usage_summary(
    app_label: str,
    subject_type: str,
    subject_id: str,
    metric_codes=None,
) -> list[dict]:
    """
    Return a list of usage summary dicts for all metrics of an app for a subject.
    Each dict: {metric_code, unit, period, total, limit, pct_used, mode}
    Used by each app's metrics view.
    """
    from .models import QuotaPolicy, UsageEvent

    policy_qs = QuotaPolicy.objects.filter(
        app_label=app_label,
        active=True,
    ).filter(
        Q(subject_type="") | Q(subject_type=subject_type, subject_id=subject_id)
    )
    if metric_codes:
        policy_qs = policy_qs.filter(metric_code__in=metric_codes)

    event_codes_qs = UsageEvent.objects.filter(
        app_label=app_label,
        subject_type=subject_type,
        subject_id=subject_id,
        status="recorded",
    )
    if metric_codes:
        event_codes_qs = event_codes_qs.filter(metric_code__in=metric_codes)
    event_codes = set(event_codes_qs.values_list("metric_code", flat=True).distinct())

    policy_map: dict = {}
    for p in policy_qs:
        code = p.metric_code
        existing = policy_map.get(code)
        if existing is None:
            policy_map[code] = p
        elif p.subject_type and not existing.subject_type:
            policy_map[code] = p

    all_codes = set(policy_map.keys()) | event_codes

    results = []
    for code in sorted(all_codes):
        policy = policy_map.get(code)
        period = policy.period if policy else "lifetime"
        since = _period_start(period)
        total = _period_total(app_label, code, subject_type, subject_id, since)
        limit = policy.limit if policy else None
        pct_used = float(total / limit * 100) if (limit and limit > 0) else None
        results.append({
            "metric_code": code,
            "unit": policy.unit if policy else "",
            "period": period,
            "total": total,
            "limit": limit,
            "pct_used": pct_used,
            "mode": policy.mode if policy else "track",
        })

    return results
