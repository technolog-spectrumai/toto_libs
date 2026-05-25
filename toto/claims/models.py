from __future__ import annotations

import uuid as _uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------

class EntitlementStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"
    SUSPENDED = "suspended", "Suspended"


class EntitlementKind(models.TextChoices):
    SERVICE_ACCESS = "service_access", "Service Access"
    LEASE_RIGHT = "lease_right", "Lease Right"
    VESTING_RIGHT = "vesting_right", "Vesting Right"
    EXERCISE_RIGHT = "exercise_right", "Exercise Right"
    REWARD_ELIGIBILITY = "reward_eligibility", "Reward Eligibility"
    CLAIM_RIGHT = "claim_right", "Claim Right"
    USAGE_RIGHT = "usage_right", "Usage Right"
    OTHER = "other", "Other"


class Entitlement(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        "assets.Agreement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="entitlements",
    )
    contract = models.ForeignKey(
        "assets.Contract", null=True, blank=True, on_delete=models.PROTECT,
        related_name="entitlements",
    )
    holder_account = models.ForeignKey(
        "assets.LedgerAccount", on_delete=models.PROTECT, related_name="entitlements",
    )
    resource_label = models.CharField(max_length=255)
    kind = models.CharField(max_length=40, choices=EntitlementKind.choices)
    status = models.CharField(
        max_length=20, choices=EntitlementStatus.choices,
        default=EntitlementStatus.ACTIVE,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["holder_account", "status"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.resource_label} ({self.holder_account.code})"


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

class ScheduleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ScheduleKind(models.TextChoices):
    BILLING = "billing", "Billing"
    RENEWAL = "renewal", "Renewal"
    EXPIRY = "expiry", "Expiry"
    VESTING = "vesting", "Vesting"
    PAYOUT = "payout", "Payout"
    SETTLEMENT = "settlement", "Settlement"
    REWARD = "reward", "Reward"
    REVIEW = "review", "Review"
    CHECKPOINT = "checkpoint", "Checkpoint"
    OTHER = "other", "Other"


class ScheduleQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=ScheduleStatus.ACTIVE)

    def due(self, now=None):
        from django.utils import timezone as _tz
        if now is None:
            now = _tz.now()
        return self.filter(status=ScheduleStatus.ACTIVE, next_run_at__lte=now)

    def by_kind(self, kind):
        return self.filter(kind=kind)

    def for_source(self, source_type, source_id):
        return self.filter(source_type=source_type, source_id=str(source_id))


class Schedule(models.Model):
    objects = ScheduleQuerySet.as_manager()

    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        "assets.Agreement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="schedules",
    )
    contract = models.ForeignKey(
        "assets.Contract", null=True, blank=True, on_delete=models.PROTECT,
        related_name="schedules",
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=30, choices=ScheduleKind.choices)
    status = models.CharField(
        max_length=20, choices=ScheduleStatus.choices,
        default=ScheduleStatus.DRAFT,
    )
    frequency = models.CharField(
        max_length=50, blank=True,
        help_text="once / daily / weekly / monthly / quarterly / yearly / custom",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_run_at", "starts_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status", "next_run_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})
        has_ref = (
            self.agreement_id
            or self.contract_id
            or (self.source_type and self.source_id)
        )
        if not has_ref and not (self.metadata or {}).get("manual"):
            raise ValidationError(
                "Schedule must reference an agreement, contract, or source_type+source_id "
                "(or set metadata.manual=true)."
            )


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

class ConditionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SATISFIED = "satisfied", "Satisfied"
    FAILED = "failed", "Failed"
    WAIVED = "waived", "Waived"
    CANCELLED = "cancelled", "Cancelled"


class ConditionKind(models.TextChoices):
    TIME = "time", "Time"
    STATUS = "status", "Status"
    APPROVAL = "approval", "Approval"
    BALANCE = "balance", "Balance"
    EVIDENCE = "evidence", "Evidence"
    THRESHOLD = "threshold", "Threshold"
    MANUAL = "manual", "Manual"
    EXTERNAL = "external", "External"
    OTHER = "other", "Other"


class Condition(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        "assets.Agreement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="conditions",
    )
    contract = models.ForeignKey(
        "assets.Contract", null=True, blank=True, on_delete=models.PROTECT,
        related_name="conditions",
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=30, choices=ConditionKind.choices)
    status = models.CharField(
        max_length=20, choices=ConditionStatus.choices,
        default=ConditionStatus.PENDING,
    )
    description = models.TextField(blank=True)
    expression = models.JSONField(default=dict, blank=True)
    satisfied_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_status_display()}]"

    def clean(self):
        has_ref = (
            self.agreement_id
            or self.contract_id
            or (self.source_type and self.source_id)
        )
        if not has_ref and not (self.metadata or {}).get("manual"):
            raise ValidationError(
                "Condition must reference an agreement, contract, or source_type+source_id "
                "(or set metadata.manual=true)."
            )


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

class AllocationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    RELEASED = "released", "Released"
    CONSUMED = "consumed", "Consumed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


class AllocationKind(models.TextChoices):
    RESERVE = "reserve", "Reserve"
    ESCROW_HOLD = "escrow_hold", "Escrow Hold"
    COLLATERAL = "collateral", "Collateral"
    MARGIN = "margin", "Margin"
    SPLIT = "split", "Split"
    WATERFALL = "waterfall", "Waterfall"
    STAKING_LOCK = "staking_lock", "Staking Lock"
    VESTING_POOL = "vesting_pool", "Vesting Pool"
    PREPAID_BALANCE = "prepaid_balance", "Prepaid Balance"
    BUDGET = "budget", "Budget"
    OTHER = "other", "Other"


class Allocation(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        "assets.Agreement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations",
    )
    contract = models.ForeignKey(
        "assets.Contract", null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations",
    )
    holder_account = models.ForeignKey(
        "assets.LedgerAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations_as_holder",
    )
    beneficiary_account = models.ForeignKey(
        "assets.LedgerAccount", null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations_as_beneficiary",
    )
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="allocations")
    amount_base_units = models.BigIntegerField()
    allocated_amount_base_units = models.BigIntegerField(default=0)
    released_amount_base_units = models.BigIntegerField(default=0)
    consumed_amount_base_units = models.BigIntegerField(default=0)
    kind = models.CharField(max_length=30, choices=AllocationKind.choices)
    status = models.CharField(
        max_length=20, choices=AllocationStatus.choices,
        default=AllocationStatus.DRAFT,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount_base_units} {self.asset.unit_name}"

    def clean(self):
        if self.amount_base_units is not None and self.amount_base_units <= 0:
            raise ValidationError({"amount_base_units": "amount_base_units must be > 0."})
        for fname in ("allocated_amount_base_units", "released_amount_base_units", "consumed_amount_base_units"):
            v = getattr(self, fname)
            if v is not None and v < 0:
                raise ValidationError({fname: f"{fname} cannot be negative."})
        released = self.released_amount_base_units or 0
        consumed = self.consumed_amount_base_units or 0
        total = self.amount_base_units or 0
        if released + consumed > total:
            raise ValidationError(
                "released_amount_base_units + consumed_amount_base_units cannot exceed amount_base_units."
            )
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})

    @property
    def remaining_amount_base_units(self) -> int:
        return (
            self.amount_base_units
            - self.released_amount_base_units
            - self.consumed_amount_base_units
        )

    @property
    def is_active_now(self) -> bool:
        from django.utils import timezone as _tz
        if self.status != AllocationStatus.ACTIVE:
            return False
        now = _tz.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


# ---------------------------------------------------------------------------
# ContractEvent
# ---------------------------------------------------------------------------

class ContractEventKind(models.TextChoices):
    CREATED = "created", "Created"
    ACTIVATED = "activated", "Activated"
    PAUSED = "paused", "Paused"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    RENEWED = "renewed", "Renewed"
    PAYMENT_DUE = "payment_due", "Payment Due"
    PAYMENT_PAID = "payment_paid", "Payment Paid"
    PAYMENT_FAILED = "payment_failed", "Payment Failed"
    ENTITLEMENT_GRANTED = "entitlement_granted", "Entitlement Granted"
    ENTITLEMENT_REVOKED = "entitlement_revoked", "Entitlement Revoked"
    OBLIGATION_CREATED = "obligation_created", "Obligation Created"
    OBLIGATION_FULFILLED = "obligation_fulfilled", "Obligation Fulfilled"
    CONDITION_SATISFIED = "condition_satisfied", "Condition Satisfied"
    CONDITION_FAILED = "condition_failed", "Condition Failed"
    ALLOCATION_CREATED = "allocation_created", "Allocation Created"
    ALLOCATION_RELEASED = "allocation_released", "Allocation Released"
    ALLOCATION_CONSUMED = "allocation_consumed", "Allocation Consumed"
    SETTLEMENT = "settlement", "Settlement"
    DEFAULT = "default", "Default"
    CUSTOM = "custom", "Custom"


class ContractEvent(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        "assets.Agreement", null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    contract = models.ForeignKey(
        "assets.Contract", null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    kind = models.CharField(max_length=40, choices=ContractEventKind.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="contract_events",
    )
    transaction = models.ForeignKey(
        "assets.LedgerTransaction", null=True, blank=True, on_delete=models.PROTECT,
        related_name="contract_events",
    )
    obligation = models.ForeignKey(
        "assets.Obligation", null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    entitlement = models.ForeignKey(
        Entitlement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    schedule = models.ForeignKey(
        Schedule, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    condition = models.ForeignKey(
        Condition, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    allocation = models.ForeignKey(
        Allocation, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"

    def clean(self):
        has_ref = (
            self.agreement_id
            or self.contract_id
            or self.transaction_id
            or self.obligation_id
            or self.entitlement_id
            or self.schedule_id
            or self.condition_id
            or self.allocation_id
            or (self.source_type and self.source_id)
        )
        if not has_ref and not (self.payload or {}).get("manual"):
            raise ValidationError(
                "ContractEvent must reference at least one object or source, "
                "or set payload.manual=true."
            )
