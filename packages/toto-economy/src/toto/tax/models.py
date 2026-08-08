"""What the levy engine remembers: rules and arrears.

Deliberately thin. A rule carries only what nothing else stores — the free
allowance. The price lives on the tariffs rate card, the measurement in the
provider app, the daily usage trail in that app's quota event table. The join
between all of them is the metric code string, same as everywhere else in
metering, so this app holds no foreign key into tariffs, assets or toto-base.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from toto.quota.models import AbstractQuotaPolicy, AbstractUsageEvent


class TaxRule(models.Model):
    """One recurring levy: a metric, and the allowance under which it is free.

    ``allowance`` is in the metric's billing units (GB for ``storage.gb_day``);
    only holdings above it are levied, and enforcement sheds back down to it.
    """

    metric_code = models.CharField(
        max_length=100, unique=True,
        help_text="The registered quota metric this levy bills through.",
    )
    allowance = models.DecimalField(
        max_digits=30, decimal_places=10, default=Decimal("0"),
        help_text="Held for free, in billing units (e.g. GB). Only the excess is levied.",
    )
    unit_label = models.CharField(
        max_length=32, blank=True,
        help_text="How the allowance unit is written for people, e.g. 'GB'.",
    )
    active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["metric_code"]

    def __str__(self):
        return f"{self.metric_code} (free ≤ {self.allowance} {self.unit_label})".strip()


class TimeGrant(models.Model):
    """A user's raised time dial: one limit held above its free default.

    ``user`` is always the BILLING owner. For workspace-scoped keys it is
    denormalized to the workspace owner at write time (only the owner may set
    a workspace's dial — timegrants.set_grant enforces it), so the daily
    demurrage sample never joins into a host app's table. Note: if a
    workspace ever changes owners, the grant keeps billing the old owner
    until the dial is next touched — there is no owner-transfer flow today.

    ``scope_id`` is a soft reference (no FK — toto-economy must not import a
    host app); the declaration's ``scope_model`` string says what it names,
    and the levy provider prunes grants whose object has vanished.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="time_grants",
    )
    key = models.CharField(max_length=100, db_index=True)  # TimeLimit.key
    scope_id = models.BigIntegerField(null=True, blank=True)
    seconds = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "key"],
                condition=Q(scope_id__isnull=True),
                name="tax_timegrant_one_per_user",
            ),
            models.UniqueConstraint(
                fields=["key", "scope_id"],
                condition=Q(scope_id__isnull=False),
                name="tax_timegrant_one_per_scope",
            ),
        ]
        indexes = [models.Index(fields=["user", "key"])]
        ordering = ["key", "scope_id"]

    def __str__(self):
        scope = f"#{self.scope_id}" if self.scope_id is not None else "account"
        return f"{self.user} / {self.key} ({scope}) = {self.seconds}s"


class TaxUsageEvent(AbstractUsageEvent):
    """Levy events for resources toto.tax itself owns (time.hold)."""

    class Meta(AbstractUsageEvent.Meta):
        verbose_name = "Tax usage event"
        verbose_name_plural = "Tax usage events"


class TaxQuotaPolicy(AbstractQuotaPolicy):
    events = TaxUsageEvent

    class Meta(AbstractQuotaPolicy.Meta):
        verbose_name = "Tax quota policy"
        verbose_name_plural = "Tax quota policies"


class ArrearsStatus(models.TextChoices):
    OPEN = "open", "Open"            # charge failed; warning not yet delivered
    WARNED = "warned", "Warned"      # warning delivered; deadline running
    RESOLVED = "resolved", "Resolved"  # a later charge succeeded / back under allowance
    ENFORCED = "enforced", "Enforced"  # holdings were shed; terminal even if partial


class TaxArrearsCase(models.Model):
    """One user's unpaid levy, from first failed day to resolution or enforcement.

    The partial unique constraint is the state machine's backbone: at most one
    live case per (user, rule). RESOLVED and ENFORCED are terminal, so a later
    failure opens a fresh case — and with it a fresh warning and a fresh week.
    No amount owed is stored anywhere: failed days are written off, and the
    shortfall fields are a snapshot for the warning text, not a debt.
    """

    uuid = models.UUIDField(default=uuid4, unique=True, editable=False, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="tax_arrears_cases",
    )
    rule = models.ForeignKey(TaxRule, on_delete=models.CASCADE, related_name="arrears_cases")
    status = models.CharField(
        max_length=16, choices=ArrearsStatus.choices,
        default=ArrearsStatus.OPEN, db_index=True,
    )

    opened_at = models.DateTimeField(default=timezone.now)
    warned_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    enforced_at = models.DateTimeField(null=True, blank=True)
    resolution_reason = models.CharField(max_length=32, blank=True)  # "paid" | "under_allowance"

    # Warning delivery. A soft reference: the events tables live in toto-base
    # and the event may be deleted (it is, on resolution) without our knowledge.
    warning_event_uid = models.UUIDField(null=True, blank=True)
    warning_channel = models.CharField(max_length=16, blank=True)  # "event" | "none"

    # Snapshot of the most recent failed day, for warnings and staff screens.
    last_failure_at = models.DateTimeField(null=True, blank=True)
    failed_days = models.PositiveIntegerField(default=0)
    last_shortfall_display = models.DecimalField(
        max_digits=30, decimal_places=18, null=True, blank=True,
    )
    last_shortfall_asset = models.CharField(max_length=32, blank=True)
    last_stored_raw = models.PositiveBigIntegerField(default=0)
    allowance_raw_at_open = models.PositiveBigIntegerField(default=0)

    # Enforcement outcome. reached_target=False means everything left was
    # protected — staff-visible in admin; the case is still terminal.
    reached_target = models.BooleanField(null=True, blank=True)
    enforcement_summary = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "rule"],
                condition=Q(status__in=["open", "warned"]),
                name="tax_one_active_case_per_user_rule",
            ),
        ]
        indexes = [models.Index(fields=["status", "deadline_at"])]
        ordering = ["-opened_at"]

    def __str__(self):
        return f"{self.user} / {self.rule.metric_code} — {self.status}"

    @property
    def is_active(self) -> bool:
        return self.status in (ArrearsStatus.OPEN, ArrearsStatus.WARNED)


class EnforcementAction(models.TextChoices):
    DELETED = "deleted", "Deleted"
    SKIPPED_PROTECTED = "skipped_protected", "Skipped (protected)"


class TaxEnforcementAction(models.Model):
    """One item touched by an enforcement run. The row outlives the file —
    that is its whole purpose; the pk is a plain integer, not a FK."""

    case = models.ForeignKey(TaxArrearsCase, on_delete=models.CASCADE, related_name="actions")
    action = models.CharField(max_length=24, choices=EnforcementAction.choices)
    item_pk = models.BigIntegerField()
    item_label = models.CharField(max_length=255, blank=True)
    item_key = models.CharField(max_length=255, blank=True)
    container = models.CharField(max_length=120, blank=True)  # e.g. the bucket slug
    size_raw = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.action}: {self.item_label} ({self.size_raw})"
