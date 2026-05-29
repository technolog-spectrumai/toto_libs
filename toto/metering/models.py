"""
toto.metering.models
====================

Abstract model bases only. No concrete tables, no migrations.

Each source app subclasses these to create its own metering tables:

    # vault/models.py
    from toto.metering.models import AbstractUsageEvent, AbstractUsageQuota

    class VaultUsageEvent(AbstractUsageEvent):
        class Meta(AbstractUsageEvent.Meta):
            app_label = "vault"

    class VaultUsageQuota(AbstractUsageQuota):
        class Meta(AbstractUsageQuota.Meta):
            app_label = "vault"
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# AbstractUsageEvent
# ---------------------------------------------------------------------------

class EventStatus(models.TextChoices):
    RECORDED = "recorded", _("Recorded")
    VOIDED = "voided", _("Voided")
    SUPERSEDED = "superseded", _("Superseded")


class AbstractUsageEvent(models.Model):
    """
    Base shape for all per-app usage event tables.

    Stores what was measured, how much, by what source, for which subject,
    and when. No pricing, no assets, no invoices.
    """

    metric_code = models.CharField(max_length=120, db_index=True)
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=80, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)

    source_type = models.CharField(max_length=120, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    source_label = models.CharField(max_length=255, blank=True)

    subject_type = models.CharField(max_length=120, blank=True)
    subject_id = models.CharField(max_length=255, blank=True)
    subject_label = models.CharField(max_length=255, blank=True)

    idempotency_key = models.CharField(max_length=255, blank=True, db_index=True)
    status = models.CharField(
        max_length=20, choices=EventStatus.choices, default=EventStatus.RECORDED, db_index=True
    )
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        indexes = [
            models.Index(fields=["metric_code", "occurred_at"]),
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["subject_type", "subject_id"]),
            models.Index(fields=["status"]),
        ]

    def clean(self):
        if self.quantity is not None and self.quantity <= 0:
            raise ValidationError({"quantity": _("Quantity must be positive.")})

    @property
    def is_recorded(self) -> bool:
        return self.status == EventStatus.RECORDED

    @property
    def is_voided(self) -> bool:
        return self.status == EventStatus.VOIDED

    def __str__(self):
        return f"{self.metric_code} × {self.quantity} ({self.status})"


# ---------------------------------------------------------------------------
# AbstractUsageQuota
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


class AbstractUsageQuota(models.Model):
    """
    Base shape for per-app quota tables.

    Defines the maximum allowed quantity of a metric for a subject over a period.
    No pricing, no assets.
    """

    code = models.SlugField(max_length=120)
    name = models.CharField(max_length=180)
    metric_code = models.CharField(max_length=120, db_index=True)

    subject_type = models.CharField(max_length=120, blank=True)
    subject_id = models.CharField(max_length=255, blank=True)
    subject_label = models.CharField(max_length=255, blank=True)

    limit_quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=80, blank=True)
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
        abstract = True
        indexes = [
            models.Index(fields=["metric_code", "is_active"]),
            models.Index(fields=["subject_type", "subject_id"]),
            models.Index(fields=["period", "mode"]),
        ]

    def clean(self):
        errors = {}
        if self.limit_quantity is not None and self.limit_quantity <= 0:
            errors["limit_quantity"] = _("Limit must be positive.")
        if self.period == QuotaPeriod.ROLLING and not self.rolling_seconds:
            errors["rolling_seconds"] = _("Required when period is 'rolling'.")
        if self.starts_at and self.ends_at and self.starts_at >= self.ends_at:
            errors["ends_at"] = _("ends_at must be after starts_at.")
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.code} ({self.metric_code} / {self.period})"
