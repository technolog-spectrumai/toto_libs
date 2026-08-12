"""The quota service layer.

Every function takes the app's own concrete policy model as its first argument
— there is no registry to fall out of step with the app graph, and nothing here
knows which apps exist. The event model is reached through
``policy_model.events``, so a call site names one class.

Two behaviours are worth knowing before you rely on them:

* **No policy means allowed.** An unmetered metric is free, and a metric whose
  policy was deleted becomes free again. Enforcement is opt-in per metric, by
  the presence of a row.
* **Checking is not spending.** :func:`check_quota` reads; :func:`record_usage`
  writes. Between the two, a concurrent request can consume the same headroom.
  That is fine for cost control and wrong for anything that must never be
  exceeded.

Periods are **calendar-aligned**: a daily limit resets at midnight rather than
sliding 24 hours behind the last request, a week starts on Monday, and a month
starts on the 1st whatever its length.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from .choices import EventStatus, Mode, Period

logger = logging.getLogger("toto.quota")


class ImproperlyConfiguredQuota(Exception):
    """A policy model was declared without its paired event model."""


class QuotaExceeded(Exception):
    """Raised by :func:`check_quota` when a blocking policy would be exceeded."""

    #: What an HTTP layer should answer with. Quota is a rate problem, not a
    #: payment one — see toto.quota.charge for the 402 case.
    status_code = 429

    def __init__(self, policy, current: Decimal, limit: Decimal) -> None:
        self.policy = policy
        self.current = current
        self.limit = limit

    def __str__(self) -> str:
        p = self.policy
        per = f" per {p.period}" if p.period != Period.LIFETIME else ""
        return (
            f"{p.name or p.metric_code} quota exceeded "
            f"(used {self.current}/{self.limit} {p.unit or 'units'}{per})"
        )


# ---------------------------------------------------------------------------
# Periods
# ---------------------------------------------------------------------------

def period_start(period: str, at=None):
    """The start of the current window, or None for a lifetime policy.

    Calendar-aligned, in the project's timezone. The alternative — subtracting
    a timedelta from now — never resets: a user who hits a daily cap stays
    capped for a rolling 24 hours from each request rather than until midnight.
    """
    if period == Period.LIFETIME:
        return None

    now = at or timezone.now()
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)

    if period == Period.DAILY:
        return day
    if period == Period.WEEKLY:
        return day - timedelta(days=now.weekday())   # Monday
    if period == Period.MONTHLY:
        return day.replace(day=1)
    if period == Period.YEARLY:
        return day.replace(month=1, day=1)
    return day


def _event_model(policy_model):
    events = getattr(policy_model, "events", None)
    if events is None:
        raise ImproperlyConfiguredQuota(
            f"{policy_model.__name__} does not name its usage-event model. "
            f"Set `events = <YourUsageEvent>` on it."
        )
    return events


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def get_policy(policy_model, metric_code: str, user=None):
    """The policy governing this metric for this user, or None.

    A row naming the user wins; otherwise the default row (``user=None``).
    """
    now = timezone.now()
    base = policy_model.objects.filter(metric_code=metric_code, active=True)
    base = base.exclude(starts_at__gt=now).exclude(ends_at__lte=now)

    if user is not None and getattr(user, "pk", None):
        specific = base.filter(user=user).first()
        if specific is not None:
            return specific
    return base.filter(user__isnull=True).first()


def effective_limit(policy, user=None) -> Decimal:
    """The limit that actually applies to this user: the policy's, times the
    headroom their offices buy them.

    A :class:`~toto.socialhub.models.Station` carries a ``limit_multiplier``, so
    an office has the room its work needs — the archivist uploads more than a
    member because the job requires it. **Headroom only.** No price and no tax
    reads this: a holder pays exactly what anyone else pays for the same action,
    which is the whole of "extra limits, same taxes".

    Read through one function rather than at each comparison so the answer
    cannot drift between what a page displays and what the gate enforces.
    Degrades to the plain limit on any failure, and never raises: a quota check
    that blew up on a missing socialhub row would take down every metered
    action on the host.
    """
    limit = Decimal(policy.limit)
    if user is None or not getattr(user, "pk", None):
        return limit
    try:
        from toto.socialhub import privileges

        multiplier = privileges.limit_multiplier_for(user)
    except Exception:  # noqa: BLE001 - no office is the commoner answer
        return limit
    if multiplier == 1:
        return limit
    return limit * Decimal(multiplier)


def used(policy_model, metric_code: str, user=None, *, period=Period.LIFETIME, at=None) -> Decimal:
    """How much of this metric the user has consumed in the current window."""
    events = _event_model(policy_model)
    qs = events.objects.filter(metric_code=metric_code, status=EventStatus.RECORDED)
    qs = qs.filter(user=user) if user is not None else qs.filter(user__isnull=True)

    since = period_start(period, at)
    if since is not None:
        qs = qs.filter(occurred_at__gte=since)
    return qs.aggregate(total=Sum("quantity"))["total"] or Decimal("0")


def remaining(policy_model, metric_code: str, user=None) -> Decimal | None:
    """Headroom left, or None when the metric is unmetered."""
    policy = get_policy(policy_model, metric_code, user)
    if policy is None:
        return None
    consumed = used(policy_model, metric_code, user, period=policy.period)
    return max(Decimal("0"), effective_limit(policy, user) - consumed)


# ---------------------------------------------------------------------------
# Enforcing
# ---------------------------------------------------------------------------

def check_quota(policy_model, metric_code: str, quantity=1, user=None) -> None:
    """Raise :class:`QuotaExceeded` if consuming this much would break a limit.

    Silent when no policy exists or the policy is not in block mode. The test
    is prospective — consuming exactly up to the limit is allowed.
    """
    policy = get_policy(policy_model, metric_code, user)
    if policy is None or policy.mode != Mode.BLOCK:
        return

    limit = effective_limit(policy, user)
    consumed = used(policy_model, metric_code, user, period=policy.period)
    if consumed + Decimal(str(quantity)) > limit:
        raise QuotaExceeded(policy, consumed, limit)


def record_usage(
    event_model,
    metric_code: str,
    quantity,
    user=None,
    *,
    unit: str = "",
    source_type: str = "",
    source_id: str = "",
    source_label: str = "",
    idempotency_key: str = "",
    metadata=None,
    occurred_at=None,
):
    """Record one usage action. Never raises — metering must not break a request.

    Returns the event, or None when it was a duplicate or the write failed.
    Those two are deliberately indistinguishable to the caller: neither is an
    error it can do anything about.
    """
    try:
        if idempotency_key:
            existing = event_model.objects.filter(idempotency_key=idempotency_key).first()
            if existing is not None:
                return None
        return event_model.objects.create(
            metric_code=metric_code,
            quantity=Decimal(str(quantity)),
            unit=unit,
            user=user if getattr(user, "pk", None) else None,
            source_type=source_type,
            source_id=source_id,
            source_label=source_label,
            idempotency_key=idempotency_key,
            metadata=metadata or {},
            occurred_at=occurred_at or timezone.now(),
        )
    except Exception:  # noqa: BLE001 - logged, never surfaced to the caller
        logger.exception("quota: could not record %s x%s", metric_code, quantity)
        return None


def usage_summary(policy_model, user=None, metric_codes=None) -> list[dict]:
    """Dashboard rows: one per metric that has either a policy or any usage."""
    events = _event_model(policy_model)

    policies = {p.metric_code: p for p in policy_model.objects.filter(active=True)}
    if user is not None and getattr(user, "pk", None):
        for p in policy_model.objects.filter(active=True, user=user):
            policies[p.metric_code] = p

    seen = set(policies)
    event_qs = events.objects.filter(status=EventStatus.RECORDED)
    event_qs = event_qs.filter(user=user) if user is not None else event_qs.filter(user__isnull=True)
    seen |= set(event_qs.values_list("metric_code", flat=True).distinct())

    if metric_codes is not None:
        seen &= set(metric_codes)

    rows = []
    for code in sorted(seen):
        policy = policies.get(code)
        period = policy.period if policy else Period.LIFETIME
        total = used(policy_model, code, user, period=period)
        # The same number check_quota will enforce, office headroom included —
        # a dashboard quoting the bare policy limit would tell a station holder
        # they are at 100% while uploads keep succeeding.
        limit = effective_limit(policy, user) if policy else None
        rows.append({
            "metric_code": code,
            "unit": policy.unit if policy else "",
            "period": period,
            "total": total,
            "limit": limit,
            "pct_used": (float(total / limit * 100) if limit else None),
            "mode": policy.mode if policy else Mode.TRACK,
        })
    return rows
