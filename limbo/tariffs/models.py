from __future__ import annotations

import uuid as _uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# BillingUnit
# ---------------------------------------------------------------------------

class BillingUnit(models.Model):
    code = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=255)
    dimension = models.CharField(max_length=50, blank=True)
    app_label = models.CharField(max_length=100, blank=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["dimension", "code"]

    def __str__(self):
        return f"{self.code} — {self.label}"


# ---------------------------------------------------------------------------
# BillingMetric
# ---------------------------------------------------------------------------

class BillingMetric(models.Model):
    """
    A first-class metric descriptor. TariffItem.metric references one of these
    instead of storing a raw code string. Services still accept metric_code strings
    and look up items via metric__code= so callers need no changes.
    """
    code = models.CharField(max_length=100, unique=True)
    label = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    default_unit = models.ForeignKey(
        "tariffs.BillingUnit",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="metrics",
    )
    dimension = models.CharField(max_length=50, blank=True)
    app_label = models.CharField(max_length=100, blank=True)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["dimension", "code"]

    def __str__(self):
        return f"{self.code} — {self.label}"


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class TariffStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ACTIVE = "active", _("Active")
    PAUSED = "paused", _("Paused")
    ARCHIVED = "archived", _("Archived")


class RoundingMode(models.TextChoices):
    UP = "up", _("Up")
    DOWN = "down", _("Down")
    NEAREST = "nearest", _("Nearest")


class UsageStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    RATED = "rated", _("Rated")
    POSTED = "posted", _("Posted")
    FAILED = "failed", _("Failed")
    REVERSED = "reversed", _("Reversed")


# ---------------------------------------------------------------------------
# Tariff
# ---------------------------------------------------------------------------

class Tariff(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=100, unique=True)
    status = models.CharField(
        max_length=20,
        choices=TariffStatus.choices,
        default=TariffStatus.DRAFT,
    )
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_tariffs",
        help_text=_("Only this user can edit the tariff and its items."),
    )
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["code"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def is_active(self) -> bool:
        return self.status == TariffStatus.ACTIVE

    @property
    def active_items(self):
        return self.items.filter(active=True)


# ---------------------------------------------------------------------------
# TariffItem
# ---------------------------------------------------------------------------

class TariffItem(models.Model):
    tariff = models.ForeignKey(Tariff, on_delete=models.CASCADE, related_name="items")
    name = models.CharField(max_length=255)
    metric = models.ForeignKey(
        "tariffs.BillingMetric",
        on_delete=models.PROTECT,
        related_name="tariff_items",
        help_text=_("Billing metric this item charges for, e.g. storage.request, ai.input_tokens"),
    )
    charged_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="tariff_items",
    )
    price_per_unit_display = models.DecimalField(
        max_digits=30,
        decimal_places=18,
        help_text=_("Human-readable price in asset display units"),
    )
    price_per_unit_base_units = models.BigIntegerField(
        help_text=_("Price in asset base units (integer)"),
    )
    unit = models.ForeignKey(
        "tariffs.BillingUnit",
        on_delete=models.PROTECT,
        related_name="tariff_items",
        null=True,
        blank=True,
    )
    unit_quantity = models.DecimalField(
        max_digits=20,
        decimal_places=6,
        default=Decimal("1"),
        help_text=_("Denominator: price applies per this many units, e.g. 1000 for per-1000-tokens pricing"),
    )
    receiving_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="tariff_items_receiving",
    )
    minimum_charge_base_units = models.BigIntegerField(
        default=0,
        help_text=_("Minimum charge per usage event, in base units (0 = no minimum)"),
    )
    rounding_mode = models.CharField(
        max_length=10,
        choices=RoundingMode.choices,
        default=RoundingMode.UP,
    )
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["metric__code"]
        unique_together = [("tariff", "metric")]
        indexes = [
            models.Index(fields=["tariff", "active"]),
        ]

    def __str__(self):
        return f"{self.tariff.code} / {self.metric.code}"

    @property
    def code(self) -> str:
        """Convenience shim — returns the metric code. Use metric__code in ORM filters."""
        return self.metric.code

    def clean(self):
        if self.price_per_unit_base_units is not None and self.price_per_unit_base_units < 0:
            raise ValidationError({"price_per_unit_base_units": _("Price must be >= 0.")})
        if self.unit_quantity is not None and self.unit_quantity <= 0:
            raise ValidationError({"unit_quantity": _("Unit quantity must be > 0.")})
        if self.charged_asset_id and not self.charged_asset.active:
            raise ValidationError({"charged_asset": _("Charged asset must be active.")})
        if self.receiving_account_id and not self.receiving_account.active:
            raise ValidationError({"receiving_account": _("Receiving account must be active.")})


# ---------------------------------------------------------------------------
# UsageRecord
# ---------------------------------------------------------------------------

class UsageRecord(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    tariff = models.ForeignKey(
        Tariff,
        on_delete=models.PROTECT,
        related_name="usage_records",
    )
    payer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="usage_records_as_payer",
    )
    # Denormalized snapshot of the metric code at time of recording — kept for
    # audit/history even if the BillingMetric row is later renamed or deleted.
    metric_code = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    rated_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20,
        choices=UsageStatus.choices,
        default=UsageStatus.PENDING,
    )
    ledger_transaction = models.ForeignKey(
        "assets.LedgerTransaction",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usage_records",
    )
    error_message = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["tariff", "status"]),
            models.Index(fields=["payer_account", "status"]),
            models.Index(fields=["metric_code"]),
            models.Index(fields=["status"]),
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["occurred_at"]),
        ]

    def __str__(self):
        return f"{self.uuid} — {self.metric_code} ({self.get_status_display()})"

    @property
    def is_posted(self) -> bool:
        return self.status == UsageStatus.POSTED

    @property
    def total_charge_base_units_by_asset(self) -> dict:
        result: dict = {}
        for charge in self.charges.select_related("charged_asset"):
            key = charge.charged_asset.unit_name
            result[key] = result.get(key, 0) + charge.amount_base_units
        return result


# ---------------------------------------------------------------------------
# UsageCharge
# ---------------------------------------------------------------------------

class UsageCharge(models.Model):
    usage_record = models.ForeignKey(
        UsageRecord,
        on_delete=models.CASCADE,
        related_name="charges",
    )
    tariff_item = models.ForeignKey(
        TariffItem,
        on_delete=models.PROTECT,
        related_name="charges",
    )
    charged_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="usage_charges",
    )
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)
    price_per_unit_base_units = models.BigIntegerField()
    amount_base_units = models.BigIntegerField()
    payer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="usage_charges_as_payer",
    )
    receiving_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="usage_charges_as_receiver",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return (
            f"{self.usage_record.uuid} / {self.tariff_item.metric.code}: "
            f"{self.amount_base_units} {self.charged_asset.unit_name}"
        )

    @property
    def amount_display(self) -> Decimal:
        from toto.assets.models import from_base_units
        return from_base_units(self.amount_base_units, self.charged_asset.decimals)


# ---------------------------------------------------------------------------
# TariffApplicationStatus
# ---------------------------------------------------------------------------

class TariffApplicationStatus(models.TextChoices):
    PREVIEWED = "previewed", _("Previewed")
    PRICED = "priced", _("Priced")
    INVOICED = "invoiced", _("Invoiced")
    FAILED = "failed", _("Failed")
    CANCELLED = "cancelled", _("Cancelled")


# ---------------------------------------------------------------------------
# TariffApplication
# ---------------------------------------------------------------------------

class TariffApplication(models.Model):
    """
    Audit record of a usage-statement-to-invoice conversion via a Tariff.
    Created once per usage statement; immutable after status=invoiced.
    """
    uid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False)
    statement_id = models.CharField(max_length=255, unique=True)
    usage_file = models.ForeignKey(
        "vault.VaultFile",
        on_delete=models.PROTECT,
        related_name="tariff_applications",
    )
    tariff = models.ForeignKey(
        Tariff,
        on_delete=models.PROTECT,
        related_name="applications",
    )

    # Auto-detected from YAML
    detected_source_app = models.CharField(max_length=100, blank=True)
    detected_subject_key = models.CharField(max_length=255, blank=True)
    detected_subject_type = models.CharField(max_length=100, blank=True)
    detected_subject_id = models.CharField(max_length=255, blank=True)
    detected_subject_label = models.CharField(max_length=255, blank=True)

    # Operator overrides
    override_source_app = models.CharField(max_length=100, blank=True)
    override_subject_label = models.CharField(max_length=255, blank=True)

    period_start = models.DateTimeField()
    period_end = models.DateTimeField()

    status = models.CharField(
        max_length=20,
        choices=TariffApplicationStatus.choices,
        default=TariffApplicationStatus.PREVIEWED,
    )

    # Invoice back-reference (set after invoice creation)
    invoice_source_type = models.CharField(max_length=100, blank=True)
    invoice_source_id = models.CharField(max_length=255, blank=True)

    title = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tariff_applications",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["statement_id"]),
            models.Index(fields=["status"]),
            models.Index(fields=["detected_source_app"]),
            models.Index(fields=["detected_subject_key"]),
        ]

    def __str__(self):
        return f"TariffApplication {self.statement_id} [{self.get_status_display()}]"

    @property
    def effective_source_app(self) -> str:
        return self.override_source_app or self.detected_source_app

    @property
    def effective_subject_label(self) -> str:
        return self.override_subject_label or self.detected_subject_label


# ---------------------------------------------------------------------------
# TariffApplicationLine
# ---------------------------------------------------------------------------

class TariffApplicationLine(models.Model):
    application = models.ForeignKey(
        TariffApplication,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    line_id = models.CharField(max_length=255)
    metric_code = models.CharField(max_length=100)
    quantity = models.DecimalField(max_digits=30, decimal_places=10)
    unit = models.CharField(max_length=100, blank=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    source_label = models.CharField(max_length=255, blank=True)
    description = models.TextField(blank=True)

    tariff_item = models.ForeignKey(
        TariffItem,
        on_delete=models.PROTECT,
        related_name="application_lines",
    )
    charged_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="application_lines",
    )
    amount_base_units = models.PositiveBigIntegerField()
    amount_display = models.DecimalField(max_digits=30, decimal_places=10)

    invoice_line_source_type = models.CharField(max_length=100, blank=True)
    invoice_line_source_id = models.CharField(max_length=255, blank=True)

    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        unique_together = [("application", "line_id")]

    def __str__(self):
        return f"{self.application.statement_id} / {self.line_id} ({self.metric_code})"
