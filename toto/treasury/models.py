"""
Budget models — lean pull-based control/reporting app.

Budget is NOT the ledger, invoice engine, tariff engine, or payment engine.
It imports financial consequences after they exist.
"""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _


# ---------------------------------------------------------------------------
# Status / direction / role choices
# ---------------------------------------------------------------------------

class BudgetStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    ACTIVE = "active", _("Active")
    CLOSED = "closed", _("Closed")
    ARCHIVED = "archived", _("Archived")


class BudgetItemStatus(models.TextChoices):
    PLANNED = "planned", _("Planned")
    APPROVED = "approved", _("Approved")
    COMMITTED = "committed", _("Committed")
    BOOKED = "booked", _("Booked")
    CANCELLED = "cancelled", _("Cancelled")
    REVERSED = "reversed", _("Reversed")


class StreamDirection(models.TextChoices):
    INFLOW = "inflow", _("Inflow")
    OUTFLOW = "outflow", _("Outflow")


class AccountRole(models.TextChoices):
    OPERATING = "operating", _("Operating")
    RESERVE = "reserve", _("Reserve")
    RECEIVABLES = "receivables", _("Receivables")
    PAYABLES = "payables", _("Payables")
    PAYROLL = "payroll", _("Payroll")
    ESCROW = "escrow", _("Escrow")
    TAX = "tax", _("Tax")
    EXTERNAL = "external", _("External")


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------

class Budget(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=BudgetStatus.choices,
        default=BudgetStatus.DRAFT,
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="budgets",
    )
    budget_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="primary_budgets",
    )
    owner_type = models.CharField(max_length=100, blank=True)
    owner_id = models.CharField(max_length=255, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_budgets",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["owner_type", "owner_id"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    @property
    def is_active(self) -> bool:
        return self.status == BudgetStatus.ACTIVE


# ---------------------------------------------------------------------------
# BudgetLedgerAccount
# ---------------------------------------------------------------------------

class BudgetLedgerAccount(models.Model):
    budget = models.ForeignKey(
        Budget,
        on_delete=models.CASCADE,
        related_name="account_bindings",
    )
    ledger_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="budget_bindings",
    )
    role = models.CharField(
        max_length=20,
        choices=AccountRole.choices,
        default=AccountRole.OPERATING,
    )
    is_default = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    can_receive = models.BooleanField(default=True)
    can_pay = models.BooleanField(default=True)
    can_commit = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_account_bindings",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["role", "ledger_account__code"]
        constraints = [
            models.UniqueConstraint(
                fields=["budget", "ledger_account", "role"],
                name="budget_unique_account_role",
            ),
        ]

    def __str__(self):
        return f"{self.budget.code} / {self.ledger_account.code} [{self.role}]"

    def clean(self):
        if self.ledger_account_id and not self.ledger_account.active:
            raise ValidationError({"ledger_account": _("Ledger account must be active.")})


# ---------------------------------------------------------------------------
# BudgetStreamType
# ---------------------------------------------------------------------------

class BudgetStreamType(models.Model):
    code = models.SlugField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    direction = models.CharField(
        max_length=10,
        choices=StreamDirection.choices,
    )
    namespace = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=100)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["sort_order", "code"]
        indexes = [
            models.Index(fields=["direction", "is_active"]),
            models.Index(fields=["namespace", "code"]),
        ]

    def __str__(self):
        return f"{self.code} ({self.get_direction_display()})"

    @property
    def is_inflow(self) -> bool:
        return self.direction == StreamDirection.INFLOW

    @property
    def is_outflow(self) -> bool:
        return self.direction == StreamDirection.OUTFLOW


# ---------------------------------------------------------------------------
# BudgetItem
# ---------------------------------------------------------------------------

class BudgetItem(models.Model):
    budget = models.ForeignKey(
        Budget,
        on_delete=models.CASCADE,
        related_name="items",
    )
    stream_type = models.ForeignKey(
        BudgetStreamType,
        on_delete=models.PROTECT,
        related_name="items",
    )
    account_binding = models.ForeignKey(
        BudgetLedgerAccount,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="items",
    )
    status = models.CharField(
        max_length=20,
        choices=BudgetItemStatus.choices,
        default=BudgetItemStatus.PLANNED,
    )

    # Free text is primary
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # Financial
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="budget_items",
    )
    amount_base_units = models.PositiveBigIntegerField()

    # Optional financial object links
    ledger_transaction = models.ForeignKey(
        "assets.LedgerTransaction",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_items",
    )
    obligation = models.ForeignKey(
        "assets.Obligation",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_items",
    )
    allocation = models.ForeignKey(
        "claims.Allocation",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_items",
    )
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_items",
    )
    counterparty = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_items",
    )

    # Source tracking
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    source_label = models.CharField(max_length=255, blank=True)
    source_url = models.CharField(max_length=500, blank=True)
    import_key = models.CharField(max_length=255, blank=True, db_index=True)
    external_reference = models.CharField(max_length=255, blank=True)

    # Paired reversal/correction
    paired_item = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="paired_with",
    )

    # Timing
    due_at = models.DateTimeField(null=True, blank=True)
    booked_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="budget_items",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["budget", "import_key"],
                condition=~models.Q(import_key=""),
                name="budget_item_unique_import_key",
            ),
        ]
        indexes = [
            models.Index(fields=["budget", "status"]),
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["stream_type", "status"]),
        ]

    def __str__(self):
        return f"{self.title} [{self.get_status_display()}] {self.amount_base_units}"

    @property
    def direction(self) -> str:
        return self.stream_type.direction if self.stream_type_id else ""

    @property
    def signed_amount_base_units(self) -> int:
        if self.stream_type_id and self.stream_type.direction == StreamDirection.OUTFLOW:
            return -int(self.amount_base_units)
        return int(self.amount_base_units)

    def clean(self):
        if self.amount_base_units is not None and self.amount_base_units <= 0:
            raise ValidationError({"amount_base_units": _("Amount must be positive.")})

        if self.asset_id and self.budget_id and self.asset_id != self.budget.asset_id:
            raise ValidationError({"asset": _("Asset must match the budget asset.")})

        if self.account_binding_id:
            binding = self.account_binding
            if binding.budget_id != self.budget_id:
                raise ValidationError({
                    "account_binding": _("Account binding must belong to the same budget.")
                })
            if not binding.is_active:
                raise ValidationError({
                    "account_binding": _("Account binding must be active.")
                })
            if self.stream_type_id:
                if self.stream_type.direction == StreamDirection.INFLOW and not binding.can_receive:
                    raise ValidationError({
                        "account_binding": _("This account binding cannot receive inflows.")
                    })
                if self.stream_type.direction == StreamDirection.OUTFLOW and not binding.can_pay:
                    raise ValidationError({
                        "account_binding": _("This account binding cannot make outflows.")
                    })
