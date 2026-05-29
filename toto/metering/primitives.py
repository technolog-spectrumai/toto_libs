"""
toto.metering.primitives
========================

Pure Python dataclasses and exceptions. No Django, no DB, no imports from
source apps. Safe to import anywhere.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError


# ---------------------------------------------------------------------------
# MetricSpec
# ---------------------------------------------------------------------------

@dataclass
class MetricSpec:
    """
    Declares a measurement type. Held in memory by the MetricRegistry.
    No DB table.
    """
    code: str
    name: str
    namespace: str = ""
    default_unit: str = ""
    description: str = ""
    metadata: Optional[dict] = field(default=None)


# ---------------------------------------------------------------------------
# UsageRecordInput
# ---------------------------------------------------------------------------

@dataclass
class UsageRecordInput:
    """
    Input value object for record_usage().
    Carries all fields needed to create an event row.
    """
    metric_code: str
    quantity: Decimal
    unit: str = ""
    occurred_at: Optional[datetime] = None
    source_type: str = ""
    source_id: str = ""
    source_label: str = ""
    subject_type: str = ""
    subject_id: str = ""
    subject_label: str = ""
    idempotency_key: str = ""
    description: str = ""
    metadata: Optional[dict] = field(default=None)


# ---------------------------------------------------------------------------
# QuotaDecision
# ---------------------------------------------------------------------------

@dataclass
class QuotaDecision:
    """Returned by evaluate_quota(). Communicates whether the usage is allowed."""

    allowed: bool
    mode: str                          # "track" | "warn" | "block"
    quota_id: Optional[int]
    quota_code: str
    used_quantity: Decimal
    requested_quantity: Decimal
    limit_quantity: Decimal
    remaining_quantity: Decimal
    period_start: Optional[datetime]
    period_end: Optional[datetime]
    reason: str = ""


# ---------------------------------------------------------------------------
# QuotaExceeded
# ---------------------------------------------------------------------------

class QuotaExceeded(ValidationError):
    """
    Raised by record_usage() when a blocking quota would be exceeded.
    Carries the full QuotaDecision for the caller to inspect.
    """
    def __init__(self, decision: QuotaDecision):
        self.decision = decision
        super().__init__(
            f"Quota '{decision.quota_code}' exceeded: "
            f"used={decision.used_quantity}, "
            f"requested={decision.requested_quantity}, "
            f"limit={decision.limit_quantity}."
        )
