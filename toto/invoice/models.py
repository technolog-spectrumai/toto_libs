from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class InvoiceStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    PAID = "paid", _("Paid")
    OVERDUE = "overdue", _("Overdue")
    CANCELLED = "cancelled", _("Cancelled")


class BillingCycleFrequency(models.TextChoices):
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")
    MONTHLY = "monthly", _("Monthly")
    YEARLY = "yearly", _("Yearly")


class BillingCycle(models.Model):
    """Defines how often invoices are settled for a given context (e.g. vault bucket)."""
    name = models.CharField(max_length=100, unique=True)
    frequency = models.CharField(
        max_length=20,
        choices=BillingCycleFrequency.choices,
        default=BillingCycleFrequency.MONTHLY,
    )
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Billing Cycle"
        verbose_name_plural = "Billing Cycles"
        ordering = ["frequency", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_frequency_display()})"

    @property
    def hours_per_period(self) -> int:
        return {
            BillingCycleFrequency.DAILY: 24,
            BillingCycleFrequency.WEEKLY: 168,
            BillingCycleFrequency.MONTHLY: 730,
            BillingCycleFrequency.YEARLY: 8760,
        }.get(self.frequency, 730)


class Invoice(models.Model):
    """
    Reusable invoice model. Can be generated from any app (vault, bazaar, etc.).
    Optionally linked to a vault Bucket for tariff-based billing.
    """
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="invoices",
    )
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="issued_invoices",
    )
    bucket = models.ForeignKey(
        "vault.Bucket",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoice_app_invoices",
        help_text="Vault bucket this invoice was generated for (if any).",
    )
    billing_cycle = models.ForeignKey(
        BillingCycle,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invoices",
        help_text="Billing cycle that triggered this invoice (if any).",
    )
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency_label = models.CharField(
        max_length=20,
        default="USD",
        help_text="Currency code or token unit name, e.g. USD, PLN, STORAGE_TOKEN.",
    )
    status = models.CharField(
        max_length=20,
        choices=InvoiceStatus.choices,
        default=InvoiceStatus.PENDING,
    )
    due_date = models.DateField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    obligation_reference = models.CharField(
        max_length=255,
        blank=True,
        help_text="Reference of the assets Obligation created when the invoice was accepted.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Invoice #{self.pk} — {self.issued_to.username} — {self.amount} {self.currency_label} [{self.status}]"

    @property
    def is_paid(self) -> bool:
        return self.status == InvoiceStatus.PAID

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        if self.status != InvoiceStatus.PENDING:
            return False
        return bool(self.due_date and timezone.now().date() > self.due_date)


class PaymentSettlement(models.Model):
    """Records an actual payment made against an invoice."""
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="settlements",
    )
    settled_at = models.DateTimeField()
    amount = models.DecimalField(max_digits=18, decimal_places=2)
    currency_label = models.CharField(max_length=20)
    method = models.CharField(max_length=100, blank=True, help_text="e.g. bank_transfer, ledger, cash")
    reference = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Payment Settlement"
        verbose_name_plural = "Payment Settlements"
        ordering = ["-settled_at"]

    def __str__(self):
        return f"Settlement #{self.pk} — Invoice #{self.invoice_id} — {self.amount} {self.currency_label}"
