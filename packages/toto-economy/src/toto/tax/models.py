"""What the levy engine remembers: rules and arrears.

Deliberately thin. A rule carries only what nothing else stores — which metric
is levied, and whether it is armed. The price lives on the tariffs rate card,
the measurement in the provider app, the daily usage trail in that app's quota
event table. The join
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
    """One recurring levy: a metric, billed from the first unit held.

    **There is no allowance.** A free per-metric band is shareable — the way to
    use one is to route work through somebody whose band is unspent — and it
    forced every reader of a levy (the sweep, the estimate, the audit metadata,
    the arrears snapshot) to carry a second number that had to agree everywhere.
    The free tier is the ABSENCE OF A PRICE: a metric with no price row costs
    nothing, for everyone, and deleting the row makes it free again.
    """

    metric_code = models.CharField(
        max_length=100, unique=True,
        help_text="The registered quota metric this levy bills through.",
    )
    unit_label = models.CharField(
        max_length=32, blank=True,
        help_text="How the billing unit is written for people, e.g. 'GB'.",
    )
    active = models.BooleanField(default=True)
    concentration_k = models.DecimalField(
        max_digits=12, decimal_places=4, default=Decimal("0"),
        help_text=(
            "Anti-concentration dial. Adds k × share² billing units to what each "
            "holder owes, where share is their holdings ÷ what all users hold "
            "between them. Quadratic so it is negligible for ordinary members "
            "and acute at real concentration, and continuous so there is no "
            "threshold to sit just under: at k=100 a 1% holder pays +0.01, a "
            "10% holder +1, a 50% holder +25. 0 switches it off entirely, which "
            "is the default and costs the nightly run nothing."
        ),
    )
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["metric_code"]

    def __str__(self):
        state = "armed" if self.active else "unarmed"
        return f"{self.metric_code} ({state})"


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
    RESOLVED = "resolved", "Resolved"  # a later charge succeeded, or nothing left to bill


class TaxArrearsCase(models.Model):
    """One user's unpaid levy, from the first failed day to its resolution.

    The partial unique constraint is the state machine's backbone: at most one
    live case per (user, rule). RESOLVED is terminal, so a later failure opens a
    fresh case — and with it a fresh warning and a fresh week.

    No amount owed is stored anywhere: failed days are written off, and the
    shortfall fields are a snapshot for the warning text, not a debt. Past the
    deadline the case makes ``arrears.is_frozen()`` answer True and new metered
    writes refuse; nothing is taken away, and paying or shedding holdings ends
    it. There is no ENFORCED state because there is no enforcement.
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
    resolution_reason = models.CharField(max_length=32, blank=True)  # "paid" | "nothing_held" | "unpriced"

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