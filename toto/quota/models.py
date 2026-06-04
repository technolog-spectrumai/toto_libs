from __future__ import annotations

from django.db import models
from django.utils import timezone


class Period(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"
    LIFETIME = "lifetime", "Lifetime"


class Mode(models.TextChoices):
    TRACK = "track", "Track only"
    WARN = "warn", "Warn"
    BLOCK = "block", "Block"


class EventStatus(models.TextChoices):
    RECORDED = "recorded", "Recorded"
    VOIDED = "voided", "Voided"


class QuotaPolicy(models.Model):
    """
    Defines a usage limit for a (app_label, metric_code, subject) tuple.

    subject_type + subject_id = "" means the policy applies globally to all
    subjects for that metric. A subject-specific policy overrides the global one.
    """

    name = models.CharField(max_length=255)
    app_label = models.CharField(max_length=100, db_index=True)
    metric_code = models.CharField(max_length=100, db_index=True)

    subject_type = models.CharField(max_length=100, blank=True, db_index=True)
    subject_id = models.CharField(max_length=255, blank=True, db_index=True)

    limit = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)
    period = models.CharField(max_length=20, choices=Period.choices, default=Period.DAILY)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.BLOCK)

    active = models.BooleanField(default=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Quota Policy"
        verbose_name_plural = "Quota Policies"
        ordering = ["app_label", "metric_code"]
        constraints = [
            models.UniqueConstraint(
                fields=["app_label", "metric_code", "subject_type", "subject_id"],
                name="quota_unique_policy_per_subject_metric",
            )
        ]

    def __str__(self) -> str:
        subject = f"{self.subject_type}:{self.subject_id}" if self.subject_type else "global"
        return f"{self.app_label}/{self.metric_code} — {self.limit} {self.unit}/{self.period} [{subject}]"

    def is_active_now(self) -> bool:
        now = timezone.now()
        if not self.active:
            return False
        if self.starts_at and self.starts_at > now:
            return False
        if self.ends_at and self.ends_at <= now:
            return False
        return True


class UsageEvent(models.Model):
    """One recorded usage action."""

    app_label = models.CharField(max_length=100)
    metric_code = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)

    subject_type = models.CharField(max_length=100)
    subject_id = models.CharField(max_length=255)
    subject_label = models.CharField(max_length=255, blank=True)

    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    source_label = models.CharField(max_length=255, blank=True)

    # Unique idempotency key prevents duplicate recording; blank = no dedup.
    idempotency_key = models.CharField(max_length=512, blank=True, db_index=True)

    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.RECORDED
    )
    occurred_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Usage Event"
        verbose_name_plural = "Usage Events"
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(
                fields=["app_label", "metric_code", "occurred_at"],
                name="quota_evt_app_metric_time",
            ),
            models.Index(
                fields=["subject_type", "subject_id", "metric_code"],
                name="quota_evt_subject_metric",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.app_label}/{self.metric_code} × {self.quantity}"
            f" [{self.subject_type}:{self.subject_id}]"
        )
