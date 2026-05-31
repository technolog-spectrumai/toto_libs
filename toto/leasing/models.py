from django.core.exceptions import ValidationError
from django.db import models


class BillingPeriod(models.TextChoices):
    ONCE = "once", "Once"
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    YEARLY = "yearly", "Yearly"
    CUSTOM = "custom", "Custom"


class LeaseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    EXPIRED = "expired", "Expired"
    CANCELLED = "cancelled", "Cancelled"


class Lease(models.Model):
    contract = models.OneToOneField(
        "contracts.Contract",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="lease",
    )
    leased_object = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.PROTECT,
        related_name="leases",
    )
    lessor = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        related_name="leases_as_lessor",
    )
    lessee = models.ForeignKey(
        "people.Person",
        on_delete=models.PROTECT,
        related_name="leases_as_lessee",
    )
    lessor_account = models.ForeignKey(
        "assets.LedgerAccount",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="leases_as_lessor",
    )
    lessee_account = models.ForeignKey(
        "assets.LedgerAccount",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="leases_as_lessee",
    )
    payment_asset = models.ForeignKey(
        "assets.Asset",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="leases_as_payment",
    )
    fixed_fee_base_units = models.BigIntegerField(default=0)
    billing_period = models.CharField(
        max_length=20,
        choices=BillingPeriod.choices,
        default=BillingPeriod.MONTHLY,
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    next_billing_at = models.DateTimeField(null=True, blank=True, db_index=True)
    status = models.CharField(
        max_length=20,
        choices=LeaseStatus.choices,
        default=LeaseStatus.DRAFT,
    )
    activated_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "next_billing_at"]),
            models.Index(fields=["lessor", "status"]),
            models.Index(fields=["lessee", "status"]),
        ]

    def __str__(self):
        return f"Lease #{self.pk} — {self.leased_object}"

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})
        if (
            self.lessor_account_id
            and self.lessee_account_id
            and self.lessor_account_id == self.lessee_account_id
        ):
            raise ValidationError("Lessor and lessee accounts must differ.")
        if self.lessor_id and self.lessee_id and self.lessor_id == self.lessee_id:
            raise ValidationError("Lessor and lessee must be different people.")
