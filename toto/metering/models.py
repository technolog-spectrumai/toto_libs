from __future__ import annotations

import uuid
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# UsageMetric
# ---------------------------------------------------------------------------

class UsageMetric(models.Model):
    """Defines what is measured (e.g. storage.mb_hour, vod.playback_second)."""

    code = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=255)
    namespace = models.CharField(max_length=120, blank=True)
    default_unit = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Usage Metric"
        verbose_name_plural = "Usage Metrics"
        ordering = ["namespace", "code"]

    def __str__(self):
        return self.code


# ---------------------------------------------------------------------------
# UsageEvent
# ---------------------------------------------------------------------------

class EventStatus(models.TextChoices):
    RECORDED = "recorded", _("Recorded")
    VOIDED = "voided", _("Voided")
    SUPERSEDED = "superseded", _("Superseded")


class UsageEvent(models.Model):
    """A measured fact: metric × quantity × subject × source × time."""

    uid = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    metric = models.ForeignKey(UsageMetric, on_delete=models.PROTECT, related_name="events")
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=60, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    source_type = models.CharField(max_length=200, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    source_label = models.CharField(max_length=255, blank=True)

    subject_type = models.CharField(max_length=200, blank=True)
    subject_id = models.CharField(max_length=255, blank=True)
    subject_label = models.CharField(max_length=255, blank=True)

    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.RECORDED
    )
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Usage Event"
        verbose_name_plural = "Usage Events"
        ordering = ["-occurred_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["idempotency_key"],
                condition=Q(idempotency_key__gt=""),
                name="metering_event_unique_idempotency",
            ),
            models.CheckConstraint(
                check=Q(quantity__gt=0),
                name="metering_event_quantity_positive",
            ),
        ]

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": _("Quantity must be positive.")})

    def save(self, *args, **kwargs):
        if not self.unit and self.metric_id:
            try:
                self.unit = UsageMetric.objects.values_list(
                    "default_unit", flat=True
                ).get(pk=self.metric_id)
            except UsageMetric.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"UsageEvent({self.metric_id} × {self.quantity} @ {self.occurred_at:%Y-%m-%d})"


# ---------------------------------------------------------------------------
# UsageQuota
# ---------------------------------------------------------------------------

class QuotaPeriod(models.TextChoices):
    LIFETIME = "lifetime", _("Lifetime")
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")
    MONTHLY = "monthly", _("Monthly")
    YEARLY = "yearly", _("Yearly")
    ROLLING = "rolling", _("Rolling")


class QuotaMode(models.TextChoices):
    TRACK = "track", _("Track only")
    WARN = "warn", _("Warn when exceeded")
    BLOCK = "block", _("Block when exceeded")


class UsageQuota(models.Model):
    """Max allowed quantity of a metric for a subject over a period."""

    code = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=255)
    metric = models.ForeignKey(UsageMetric, on_delete=models.PROTECT, related_name="quotas")

    subject_type = models.CharField(max_length=200, blank=True)
    subject_id = models.CharField(max_length=255, blank=True)
    subject_label = models.CharField(max_length=255, blank=True)

    limit_quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=60, blank=True)
    period = models.CharField(
        max_length=20, choices=QuotaPeriod.choices, default=QuotaPeriod.MONTHLY
    )
    rolling_seconds = models.PositiveIntegerField(null=True, blank=True)
    mode = models.CharField(
        max_length=10, choices=QuotaMode.choices, default=QuotaMode.TRACK
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Usage Quota"
        verbose_name_plural = "Usage Quotas"
        ordering = ["metric", "code"]
        constraints = [
            models.CheckConstraint(
                check=Q(limit_quantity__gt=0),
                name="metering_quota_limit_positive",
            ),
        ]

    def clean(self):
        errors = {}
        if self.limit_quantity is not None and self.limit_quantity <= 0:
            errors["limit_quantity"] = _("Limit must be positive.")
        if self.period == QuotaPeriod.ROLLING and not self.rolling_seconds:
            errors["rolling_seconds"] = _("Required when period is rolling.")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            errors["ends_at"] = _("ends_at must be after starts_at.")
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self.unit and self.metric_id:
            try:
                self.unit = UsageMetric.objects.values_list(
                    "default_unit", flat=True
                ).get(pk=self.metric_id)
            except UsageMetric.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} ({self.period})"
