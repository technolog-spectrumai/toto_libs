"""
toto.metering.services
======================

Generic usage recording helpers. All functions accept model classes as
arguments — metering owns no concrete tables.

No pricing. No assets. No invoices. No tariffs. No ledger.
"""
from __future__ import annotations

from decimal import Decimal

from django.utils import timezone

from .models import EventStatus
from .primitives import QuotaExceeded


# ---------------------------------------------------------------------------
# record_usage
# ---------------------------------------------------------------------------

def record_usage(
    *,
    event_model,
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
    quota_model=None,
    enforce_quota: bool = True,
):
    """
    Create an event row in the source app's concrete event table.

    Parameters
    ----------
    event_model   : source app's concrete AbstractUsageEvent subclass
    quota_model   : source app's concrete AbstractUsageQuota subclass (optional)
    enforce_quota : if True and quota_model provided, raises QuotaExceeded on BLOCK

    Returns the created (or existing idempotent) event instance.
    """
    quantity = Decimal(str(quantity))

    # Idempotency: return existing event if key already recorded
    if idempotency_key:
        existing = event_model.objects.filter(idempotency_key=idempotency_key).first()
        if existing:
            return existing

    # Quota check
    if quota_model is not None and enforce_quota:
        from .quotas import evaluate_quota
        decision = evaluate_quota(
            event_model=event_model,
            quota_model=quota_model,
            metric_code=metric_code,
            quantity=quantity,
            subject_type=subject_type,
            subject_id=subject_id,
            at=occurred_at,
        )
        if not decision.allowed:
            raise QuotaExceeded(decision)

    event = event_model(
        metric_code=metric_code,
        quantity=quantity,
        unit=unit,
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
    event.full_clean()
    event.save()
    return event


# ---------------------------------------------------------------------------
# void_usage_event
# ---------------------------------------------------------------------------

def void_usage_event(event, reason: str = "", actor=None):
    """
    Mark a usage event as voided. Does not delete the row.

    Appends void_reason and voided_by to metadata for auditability.
    """
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
# supersede_usage_event
# ---------------------------------------------------------------------------

def supersede_usage_event(event, replacement=None, reason: str = ""):
    """
    Mark a usage event as superseded (e.g. corrected by a new event).

    Appends superseded_by and supersede_reason to metadata.
    """
    event.status = EventStatus.SUPERSEDED
    meta = dict(event.metadata or {})
    if replacement is not None:
        meta["superseded_by"] = str(getattr(replacement, "pk", replacement))
    if reason:
        meta["supersede_reason"] = reason
    event.metadata = meta
    event.save(update_fields=["status", "metadata", "updated_at"])
    return event
