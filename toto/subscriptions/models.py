from __future__ import annotations

import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _


class TimestampedModel(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        abstract = True


class BillingInterval(models.TextChoices):
    ONCE = "once", _("Once")
    DAILY = "daily", _("Daily")
    WEEKLY = "weekly", _("Weekly")
    MONTHLY = "monthly", _("Monthly")
    QUARTERLY = "quarterly", _("Quarterly")
    YEARLY = "yearly", _("Yearly")
    CUSTOM = "custom", _("Custom")


class SubscriptionPlan(TimestampedModel):
    """Community-scoped product plan, e.g. AI Pro, Academy Basic, Storage 100GB."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        ACTIVE = "active", _("Active")
        PAUSED = "paused", _("Paused")
        ARCHIVED = "archived", _("Archived")

    class PlanKind(models.TextChoices):
        AI = "ai", _("AI")
        STORAGE = "storage", _("Storage")
        ACADEMY = "academy", _("Academy")
        COMMUNITY = "community", _("Community")
        SUPPORT = "support", _("Support")
        BUNDLE = "bundle", _("Bundle")
        CUSTOM = "custom", _("Custom")

    community = models.ForeignKey(
        "socialhub.Community",
        on_delete=models.CASCADE,
        related_name="subscription_plans",
    )
    name = models.CharField(max_length=160)
    slug = models.SlugField(max_length=180)
    code = models.SlugField(max_length=80)
    kind = models.CharField(max_length=32, choices=PlanKind.choices, default=PlanKind.CUSTOM)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    description = models.TextField(blank=True)
    is_public = models.BooleanField(default=True)
    trial_days = models.PositiveIntegerField(default=0)
    max_subscribers = models.PositiveIntegerField(null=True, blank=True)
    owner = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="owned_subscription_plans",
    )

    class Meta:
        ordering = ["community", "name"]
        constraints = [
            models.UniqueConstraint(fields=["community", "slug"], name="uniq_subscription_plan_slug_per_community"),
            models.UniqueConstraint(fields=["community", "code"], name="uniq_subscription_plan_code_per_community"),
        ]
        indexes = [
            models.Index(fields=["community", "status", "kind"]),
            models.Index(fields=["code"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.code})"

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:180]
        if not self.code:
            self.code = slugify(self.name)[:80]
        super().save(*args, **kwargs)


class SubscriptionPrice(TimestampedModel):
    """A sellable recurring/one-off price for a plan."""

    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="prices")
    name = models.CharField(max_length=120, default="Default")
    code = models.SlugField(max_length=80)
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="subscription_prices")
    amount_base_units = models.BigIntegerField(default=0)
    interval = models.CharField(max_length=24, choices=BillingInterval.choices, default=BillingInterval.MONTHLY)
    interval_count = models.PositiveIntegerField(default=1)
    setup_fee_base_units = models.BigIntegerField(default=0)
    payer_must_prepay = models.BooleanField(default=True)
    receiving_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="subscription_price_receipts",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["plan", "amount_base_units"]
        constraints = [
            models.UniqueConstraint(fields=["plan", "code"], name="uniq_subscription_price_code_per_plan"),
            models.CheckConstraint(check=models.Q(amount_base_units__gte=0), name="subscription_price_amount_nonnegative"),
            models.CheckConstraint(check=models.Q(setup_fee_base_units__gte=0), name="subscription_setup_fee_nonnegative"),
            models.CheckConstraint(check=models.Q(interval_count__gt=0), name="subscription_interval_count_positive"),
        ]
        indexes = [models.Index(fields=["plan", "is_active", "interval"])]

    def __str__(self):
        return f"{self.plan.code}/{self.code}"

    def clean(self):
        if self.interval == BillingInterval.ONCE and self.interval_count != 1:
            raise ValidationError({"interval_count": _("One-off prices must use interval_count=1.")})


class SubscriptionFeature(TimestampedModel):
    """Human-visible and machine-readable feature/limit included in a plan."""

    class FeatureKind(models.TextChoices):
        ENTITLEMENT = "entitlement", _("Entitlement")
        ALLOWANCE = "allowance", _("Allowance")
        METERED = "metered", _("Metered")
        BOOLEAN = "boolean", _("Boolean")
        SERVICE_LEVEL = "service_level", _("Service level")

    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.CASCADE, related_name="features")
    code = models.SlugField(max_length=100)
    name = models.CharField(max_length=160)
    kind = models.CharField(max_length=32, choices=FeatureKind.choices, default=FeatureKind.ENTITLEMENT)
    quantity = models.DecimalField(max_digits=18, decimal_places=6, null=True, blank=True)
    unit = models.CharField(max_length=32, blank=True, help_text=_("Examples: tokens, MB, uploads, seats, lessons."))
    reset_interval = models.CharField(max_length=24, choices=BillingInterval.choices, default=BillingInterval.MONTHLY)
    hard_limit = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["plan", "code"]
        constraints = [models.UniqueConstraint(fields=["plan", "code"], name="uniq_subscription_feature_code_per_plan")]
        indexes = [models.Index(fields=["plan", "kind", "active"])]

    def __str__(self):
        return f"{self.plan.code}:{self.code}"


class SubscriptionCustomer(TimestampedModel):
    """Billing identity for a person or organization inside a community."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        BLOCKED = "blocked", _("Blocked")
        ARCHIVED = "archived", _("Archived")

    community = models.ForeignKey("socialhub.Community", on_delete=models.CASCADE, related_name="subscription_customers")
    person = models.ForeignKey("people.Person", on_delete=models.CASCADE, related_name="subscription_customers")
    billing_account = models.ForeignKey("assets.LedgerAccount", on_delete=models.PROTECT, related_name="subscription_customer_profiles")
    default_asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="subscription_customers")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE)
    label = models.CharField(max_length=160, blank=True)

    class Meta:
        ordering = ["community", "person"]
        constraints = [models.UniqueConstraint(fields=["community", "person"], name="uniq_subscription_customer_per_community_person")]
        indexes = [models.Index(fields=["community", "status"]), models.Index(fields=["person"])]

    def __str__(self):
        return self.label or f"{self.person} subscription customer"


class Subscription(TimestampedModel):
    """Active contract between a customer and a plan price."""

    class Status(models.TextChoices):
        TRIALING = "trialing", _("Trialing")
        ACTIVE = "active", _("Active")
        PAST_DUE = "past_due", _("Past due")
        PAUSED = "paused", _("Paused")
        CANCELLED = "cancelled", _("Cancelled")
        EXPIRED = "expired", _("Expired")

    customer = models.ForeignKey(SubscriptionCustomer, on_delete=models.PROTECT, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    price = models.ForeignKey(SubscriptionPrice, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    current_period_start = models.DateTimeField(default=timezone.now)
    current_period_end = models.DateTimeField(null=True, blank=True)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)

    source_content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_sources")
    source_object_id = models.CharField(max_length=64, blank=True, default="")
    source = GenericForeignKey("source_content_type", "source_object_id")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["plan", "status"]),
            models.Index(fields=["current_period_end", "status"]),
            models.Index(fields=["source_content_type", "source_object_id"]),
        ]

    def __str__(self):
        return f"{self.customer} → {self.plan.code}"

    @property
    def asset(self):
        return self.price.asset

    def clean(self):
        if self.price_id and self.plan_id and self.price.plan_id != self.plan_id:
            raise ValidationError({"price": _("Price must belong to the selected plan.")})
        if self.customer_id and self.plan_id and self.customer.community_id != self.plan.community_id:
            raise ValidationError({"plan": _("Plan must belong to the customer's community.")})
        if self.current_period_end and self.current_period_end <= self.current_period_start:
            raise ValidationError({"current_period_end": _("Period end must be after period start.")})
        if bool(self.source_content_type_id) != bool(self.source_object_id):
            raise ValidationError(_("source_content_type and source_object_id must be set together."))


class SubscriptionEntitlement(TimestampedModel):
    """Granted access/feature state derived from plan features."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        EXPIRED = "expired", _("Expired")
        REVOKED = "revoked", _("Revoked")

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="entitlements")
    feature = models.ForeignKey(SubscriptionFeature, on_delete=models.PROTECT, related_name="entitlements")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.ACTIVE)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["subscription", "feature"]
        constraints = [models.UniqueConstraint(fields=["subscription", "feature"], name="uniq_entitlement_per_subscription_feature")]
        indexes = [models.Index(fields=["subscription", "status"]), models.Index(fields=["feature", "status"])]

    def __str__(self):
        return f"{self.subscription} / {self.feature.code}"


class SubscriptionAllowance(TimestampedModel):
    """Allowance balance for a metered feature in the current period."""

    subscription = models.ForeignKey(Subscription, on_delete=models.CASCADE, related_name="allowances")
    feature = models.ForeignKey(SubscriptionFeature, on_delete=models.PROTECT, related_name="allowances")
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    included_quantity = models.DecimalField(max_digits=24, decimal_places=6, default=Decimal("0"))
    consumed_quantity = models.DecimalField(max_digits=24, decimal_places=6, default=Decimal("0"))

    class Meta:
        ordering = ["subscription", "feature", "-period_start"]
        constraints = [
            models.UniqueConstraint(fields=["subscription", "feature", "period_start"], name="uniq_allowance_period_per_feature"),
            models.CheckConstraint(check=models.Q(included_quantity__gte=0), name="subscription_allowance_included_nonnegative"),
            models.CheckConstraint(check=models.Q(consumed_quantity__gte=0), name="subscription_allowance_consumed_nonnegative"),
        ]
        indexes = [models.Index(fields=["subscription", "period_start", "period_end"])]

    @property
    def remaining_quantity(self) -> Decimal:
        return max(Decimal("0"), self.included_quantity - self.consumed_quantity)

    def clean(self):
        if self.period_end <= self.period_start:
            raise ValidationError({"period_end": _("Period end must be after period start.")})
        if self.feature_id and self.subscription_id and self.feature.plan_id != self.subscription.plan_id:
            raise ValidationError({"feature": _("Feature must belong to the subscription plan.")})


class SubscriptionUsage(TimestampedModel):
    """A usage event against a subscription allowance, e.g. tokens, uploads, lessons."""

    class Status(models.TextChoices):
        RECORDED = "recorded", _("Recorded")
        APPLIED = "applied", _("Applied")
        OVER_LIMIT = "over_limit", _("Over limit")
        VOID = "void", _("Void")

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="usage_records")
    feature = models.ForeignKey(SubscriptionFeature, on_delete=models.PROTECT, related_name="usage_records")
    allowance = models.ForeignKey(SubscriptionAllowance, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_records")
    quantity = models.DecimalField(max_digits=24, decimal_places=6)
    unit = models.CharField(max_length=32, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.RECORDED)
    note = models.TextField(blank=True)

    source_content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_usage_sources")
    source_object_id = models.CharField(max_length=64, blank=True, default="")
    source = GenericForeignKey("source_content_type", "source_object_id")

    class Meta:
        ordering = ["-occurred_at"]
        indexes = [
            models.Index(fields=["subscription", "feature", "occurred_at"]),
            models.Index(fields=["status", "occurred_at"]),
            models.Index(fields=["source_content_type", "source_object_id"]),
        ]
        constraints = [models.CheckConstraint(check=models.Q(quantity__gt=0), name="subscription_usage_quantity_positive")]

    def clean(self):
        if self.feature_id and self.subscription_id and self.feature.plan_id != self.subscription.plan_id:
            raise ValidationError({"feature": _("Feature must belong to the subscription plan.")})
        if bool(self.source_content_type_id) != bool(self.source_object_id):
            raise ValidationError(_("source_content_type and source_object_id must be set together."))


class SubscriptionInvoice(TimestampedModel):
    """Invoice generated for a subscription period."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        OPEN = "open", _("Open")
        PAID = "paid", _("Paid")
        PARTIAL = "partial", _("Partial")
        VOID = "void", _("Void")
        FAILED = "failed", _("Failed")

    subscription = models.ForeignKey(Subscription, on_delete=models.PROTECT, related_name="invoices")
    customer = models.ForeignKey(SubscriptionCustomer, on_delete=models.PROTECT, related_name="subscription_invoices")
    price = models.ForeignKey(SubscriptionPrice, on_delete=models.PROTECT, related_name="invoices")
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.DRAFT)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    due_at = models.DateTimeField(default=timezone.now)
    subtotal_base_units = models.BigIntegerField(default=0)
    discount_base_units = models.BigIntegerField(default=0)
    tax_base_units = models.BigIntegerField(default=0)
    total_base_units = models.BigIntegerField(default=0)
    paid_base_units = models.BigIntegerField(default=0)
    error_message = models.TextField(blank=True)
    ledger_transaction = models.ForeignKey("assets.LedgerTransaction", on_delete=models.PROTECT, null=True, blank=True, related_name="subscription_invoices")

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(check=models.Q(subtotal_base_units__gte=0), name="subscription_invoice_subtotal_nonnegative"),
            models.CheckConstraint(check=models.Q(discount_base_units__gte=0), name="subscription_invoice_discount_nonnegative"),
            models.CheckConstraint(check=models.Q(tax_base_units__gte=0), name="subscription_invoice_tax_nonnegative"),
            models.CheckConstraint(check=models.Q(total_base_units__gte=0), name="subscription_invoice_total_nonnegative"),
            models.CheckConstraint(check=models.Q(paid_base_units__gte=0), name="subscription_invoice_paid_nonnegative"),
        ]
        indexes = [
            models.Index(fields=["subscription", "status"]),
            models.Index(fields=["customer", "status"]),
            models.Index(fields=["due_at", "status"]),
        ]

    def __str__(self):
        return f"Invoice {self.uid} / {self.subscription}"

    @property
    def asset(self):
        return self.price.asset

    def recalculate_totals(self):
        subtotal = sum(line.amount_base_units for line in self.lines.all())
        self.subtotal_base_units = max(0, subtotal)
        self.total_base_units = max(0, self.subtotal_base_units + self.tax_base_units - self.discount_base_units)
        return self.total_base_units

    def clean(self):
        if self.period_end <= self.period_start:
            raise ValidationError({"period_end": _("Period end must be after period start.")})
        expected_total = max(0, self.subtotal_base_units + self.tax_base_units - self.discount_base_units)
        if self.total_base_units != expected_total:
            raise ValidationError({"total_base_units": _("Total must equal subtotal + tax - discount.")})
        if self.paid_base_units > self.total_base_units:
            raise ValidationError({"paid_base_units": _("Paid amount cannot exceed total.")})


class SubscriptionInvoiceLine(TimestampedModel):
    """Invoice line item."""

    class LineKind(models.TextChoices):
        SUBSCRIPTION = "subscription", _("Subscription")
        SETUP = "setup", _("Setup fee")
        OVERAGE = "overage", _("Overage")
        ADJUSTMENT = "adjustment", _("Adjustment")
        CREDIT = "credit", _("Credit")

    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.CASCADE, related_name="lines")
    kind = models.CharField(max_length=24, choices=LineKind.choices, default=LineKind.SUBSCRIPTION)
    description = models.CharField(max_length=240)
    quantity = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    unit_amount_base_units = models.BigIntegerField(default=0)
    amount_base_units = models.BigIntegerField(default=0)

    class Meta:
        ordering = ["invoice", "created_at"]
        indexes = [models.Index(fields=["invoice", "kind"])]

    def calculate_amount(self) -> int:
        amount = Decimal(self.unit_amount_base_units) * self.quantity
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    def save(self, *args, **kwargs):
        if self.amount_base_units == 0 and self.unit_amount_base_units:
            self.amount_base_units = self.calculate_amount()
        super().save(*args, **kwargs)


class SubscriptionPayment(TimestampedModel):
    """Payment attempt/result for an invoice."""

    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        SUCCEEDED = "succeeded", _("Succeeded")
        FAILED = "failed", _("Failed")
        REVERSED = "reversed", _("Reversed")

    invoice = models.ForeignKey(SubscriptionInvoice, on_delete=models.PROTECT, related_name="payments")
    payer_account = models.ForeignKey("assets.LedgerAccount", on_delete=models.PROTECT, related_name="subscription_payments_sent")
    receiving_account = models.ForeignKey("assets.LedgerAccount", on_delete=models.PROTECT, related_name="subscription_payments_received")
    amount_base_units = models.BigIntegerField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    reference = models.CharField(max_length=180, blank=True)
    ledger_transaction = models.ForeignKey("assets.LedgerTransaction", on_delete=models.PROTECT, null=True, blank=True, related_name="subscription_payments")
    error_message = models.TextField(blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.CheckConstraint(check=models.Q(amount_base_units__gt=0), name="subscription_payment_amount_positive")]
        indexes = [models.Index(fields=["invoice", "status"]), models.Index(fields=["reference"])]

    def __str__(self):
        return f"Payment {self.amount_base_units} for {self.invoice_id}"


class SubscriptionEvent(TimestampedModel):
    """Append-only operational audit log."""

    class Kind(models.TextChoices):
        CUSTOMER_CREATED = "customer_created", _("Customer created")
        PLAN_CREATED = "plan_created", _("Plan created")
        SUBSCRIPTION_CREATED = "subscription_created", _("Subscription created")
        SUBSCRIPTION_RENEWED = "subscription_renewed", _("Subscription renewed")
        SUBSCRIPTION_CANCELLED = "subscription_cancelled", _("Subscription cancelled")
        USAGE_RECORDED = "usage_recorded", _("Usage recorded")
        INVOICE_CREATED = "invoice_created", _("Invoice created")
        INVOICE_PAID = "invoice_paid", _("Invoice paid")
        PAYMENT_FAILED = "payment_failed", _("Payment failed")
        ENTITLEMENT_GRANTED = "entitlement_granted", _("Entitlement granted")

    community = models.ForeignKey("socialhub.Community", on_delete=models.CASCADE, related_name="subscription_events")
    kind = models.CharField(max_length=40, choices=Kind.choices)
    actor = models.ForeignKey("people.Person", on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_events")
    subscription = models.ForeignKey(Subscription, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")

    target_content_type = models.ForeignKey(ContentType, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscription_event_targets")
    target_object_id = models.CharField(max_length=64, blank=True, default="")
    target = GenericForeignKey("target_content_type", "target_object_id")

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["community", "kind", "created_at"]),
            models.Index(fields=["subscription", "created_at"]),
            models.Index(fields=["target_content_type", "target_object_id"]),
        ]

    def clean(self):
        if bool(self.target_content_type_id) != bool(self.target_object_id):
            raise ValidationError(_("target_content_type and target_object_id must be set together."))
