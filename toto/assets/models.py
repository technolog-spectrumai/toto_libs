import uuid as _uuid
from decimal import Decimal, ROUND_DOWN

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


# ---------------------------------------------------------------------------
# Amount conversion helpers
# ---------------------------------------------------------------------------

def to_base_units(display_amount: Decimal, decimals: int) -> int:
    factor = Decimal(10) ** decimals
    return int((Decimal(str(display_amount)) * factor).to_integral_value())


def from_base_units(base_units: int, decimals: int) -> Decimal:
    factor = Decimal(10) ** decimals
    return (Decimal(base_units) / factor).quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN)


# ---------------------------------------------------------------------------
# Choices
# ---------------------------------------------------------------------------

class AccountType(models.TextChoices):
    USER = "user", "User"
    SYSTEM = "system", "System"
    RESERVE = "reserve", "Reserve"
    EXTERNAL = "external", "External"


class TransactionType(models.TextChoices):
    ASSET_CREATE = "asset_create", "Asset Create"
    ASSET_TRANSFER = "asset_transfer", "Asset Transfer"
    REVERSAL = "reversal", "Reversal"
    ADJUSTMENT = "adjustment", "Adjustment"


# ---------------------------------------------------------------------------
# Asset
# ---------------------------------------------------------------------------

class Asset(models.Model):
    name = models.CharField(max_length=255)
    unit_name = models.CharField(max_length=20, unique=True)
    decimals = models.PositiveSmallIntegerField()
    total_supply_base_units = models.PositiveBigIntegerField()
    active = models.BooleanField(default=True)
    is_currency = models.BooleanField(default=False, help_text="Accepted as a payment currency in the bazaar")
    backing_document = models.TextField(blank=True, help_text="What this asset is backed by (e.g. 1:1 PLN reserve held by …)")
    minting_authority = models.CharField(max_length=255, blank=True, help_text="Entity authorised to mint this asset")
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.unit_name})"

    def clean(self):
        if self.decimals is not None and self.decimals > 19:
            raise ValidationError({"decimals": "Decimals cannot exceed 19."})

    @property
    def total_supply_display(self) -> Decimal:
        return from_base_units(self.total_supply_base_units, self.decimals)


# ---------------------------------------------------------------------------
# Tokenization
# ---------------------------------------------------------------------------

class TokenizationQuerySet(models.QuerySet):
    def delete(self):
        raise ValidationError("Tokenization records are permanent and cannot be reverted.")


class TokenizationStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    DEFAULTED = "defaulted", "Defaulted"


class TokenizationDefaultReason(models.TextChoices):
    NO_LONGER_EXISTS = "no_longer_exists", "Underlying object no longer exists"
    BROKEN = "broken", "Underlying object is broken"
    LOST = "lost", "Underlying object is lost"
    OTHER = "other", "Other"


class Tokenization(models.Model):
    objects = TokenizationQuerySet.as_manager()

    real_world_object = models.ForeignKey(
        "inventory.RealWorldObject",
        on_delete=models.PROTECT,
        related_name="tokenizations",
    )
    asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="tokenizations",
    )
    supervisor = models.ForeignKey(
        "people.Person",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="supervised_tokenizations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=TokenizationStatus.choices,
        default=TokenizationStatus.ACTIVE,
    )
    default_reason = models.CharField(
        max_length=40,
        choices=TokenizationDefaultReason.choices,
        blank=True,
    )
    default_note = models.TextField(blank=True)
    defaulted_at = models.DateTimeField(null=True, blank=True)
    defaulted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="defaulted_tokenizations",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["real_world_object"],
                name="unique_tokenization_object",
            ),
            models.UniqueConstraint(
                fields=["asset"],
                name="unique_tokenization_asset",
            ),
        ]
        indexes = [
            models.Index(fields=["real_world_object"]),
            models.Index(fields=["asset"]),
            models.Index(fields=["status"]),
            models.Index(fields=["supervisor"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.real_world_object} tokenized as {self.asset.unit_name}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Tokenization records are permanent and cannot be reverted.")

    @property
    def is_defaulted(self) -> bool:
        return self.status == TokenizationStatus.DEFAULTED


# ---------------------------------------------------------------------------
# LedgerAccount
# ---------------------------------------------------------------------------

class LedgerAccount(models.Model):
    code = models.CharField(max_length=100, unique=True)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=20, choices=AccountType.choices)
    active = models.BooleanField(default=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ledger_accounts",
    )
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# AssetHolding
# ---------------------------------------------------------------------------

class AssetHolding(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="holdings")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="holdings")
    balance_base_units = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("asset", "account")]
        ordering = ["-balance_base_units"]

    def __str__(self):
        return f"{self.account.code} / {self.asset.unit_name}: {self.balance_base_units}"

    def clean(self):
        if self.balance_base_units is not None and self.balance_base_units < 0:
            raise ValidationError({"balance_base_units": "Balance cannot be negative."})

    @property
    def balance_display(self) -> Decimal:
        return from_base_units(self.balance_base_units, self.asset.decimals)


# ---------------------------------------------------------------------------
# LedgerTransaction
# ---------------------------------------------------------------------------

class LedgerTransaction(models.Model):
    reference = models.CharField(max_length=255, unique=True)
    transaction_type = models.CharField(max_length=30, choices=TransactionType.choices)
    description = models.TextField(blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    asset = models.ForeignKey(Asset, null=True, blank=True, on_delete=models.PROTECT, related_name="transactions")
    posted = models.BooleanField(default=False)
    reversed_transaction = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="reversal_set",
    )
    metadata = models.JSONField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.reference} ({self.get_transaction_type_display()})"

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                original = LedgerTransaction.objects.get(pk=self.pk)
            except LedgerTransaction.DoesNotExist:
                original = None
            if original and original.posted:
                raise ValidationError("Posted transactions are immutable.")
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# LedgerEntry
# ---------------------------------------------------------------------------

class LedgerEntry(models.Model):
    transaction = models.ForeignKey(LedgerTransaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(LedgerAccount, on_delete=models.PROTECT, related_name="entries")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="entries")
    amount_base_units = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "ledger entries"

    def __str__(self):
        sign = "+" if self.amount_base_units >= 0 else ""
        return f"{self.account.code} {sign}{self.amount_base_units} {self.asset.unit_name}"

    def clean(self):
        if self.amount_base_units == 0:
            raise ValidationError({"amount_base_units": "Amount cannot be zero."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Ledger entries are immutable after creation.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Ledger entries cannot be deleted.")

    @property
    def amount_display(self) -> Decimal:
        return from_base_units(self.amount_base_units, self.asset.decimals)


# ---------------------------------------------------------------------------
# LedgerHash
# ---------------------------------------------------------------------------

class LedgerHash(models.Model):
    transaction = models.OneToOneField(
        LedgerTransaction,
        on_delete=models.PROTECT,
        related_name="hash_record",
    )
    previous_hash = models.CharField(max_length=64, blank=True)
    hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "ledger hash"
        verbose_name_plural = "ledger hashes"

    def __str__(self):
        return f"{self.transaction.reference}: {self.hash[:16]}…"


# ---------------------------------------------------------------------------
# Currency
# ---------------------------------------------------------------------------

class Currency(models.Model):
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=5, blank=True)
    asset = models.OneToOneField(
        'Asset',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='currency_peg',
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = 'currencies'
        ordering = ['code']

    def __str__(self):
        return f"{self.code} — {self.name}"


# ---------------------------------------------------------------------------
# Obligation
# ---------------------------------------------------------------------------

class ObligationQuerySet(models.QuerySet):
    def payables_for(self, account):
        return self.filter(debtor_account=account)

    def receivables_for(self, account):
        return self.filter(creditor_account=account)

    def pending(self):
        return self.filter(status="pending")

    def overdue(self):
        from django.utils import timezone
        return self.filter(status="pending", due_at__lt=timezone.now())


class ObligationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    OVERDUE = "overdue", "Overdue"
    FULFILLED = "fulfilled", "Fulfilled"
    DEFAULTED = "defaulted", "Defaulted"


class Obligation(models.Model):
    objects = ObligationQuerySet.as_manager()

    reference = models.CharField(max_length=255, unique=True)
    order_reference = models.CharField(max_length=255, blank=True)
    debtor_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name='debtor_obligations'
    )
    creditor_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name='creditor_obligations'
    )
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name='obligations')
    amount_base_units = models.BigIntegerField()
    due_at = models.DateTimeField()
    collateral_account = models.ForeignKey(
        LedgerAccount, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='collateral_obligations'
    )
    collateral_asset = models.ForeignKey(
        Asset, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='collateral_obligations'
    )
    collateral_amount_base_units = models.BigIntegerField(default=0)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=ObligationStatus.choices, default=ObligationStatus.PENDING
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['due_at']

    def __str__(self):
        return f"{self.reference} — {self.debtor_account.code} owes {self.amount_display} {self.asset.unit_name}"

    @property
    def amount_display(self) -> Decimal:
        return from_base_units(self.amount_base_units, self.asset.decimals)

    @property
    def collateral_display(self) -> Decimal:
        if self.collateral_asset:
            return from_base_units(self.collateral_amount_base_units, self.collateral_asset.decimals)
        return Decimal(0)

    @property
    def is_overdue(self) -> bool:
        from django.utils import timezone
        return self.status == ObligationStatus.PENDING and timezone.now() > self.due_at

    def is_payable_for(self, account) -> bool:
        return self.debtor_account_id == account.pk

    def is_receivable_for(self, account) -> bool:
        return self.creditor_account_id == account.pk


# ---------------------------------------------------------------------------
# ContractTemplate
# ---------------------------------------------------------------------------

class Contract(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, editable=False, unique=True, db_index=True)
    name = models.CharField(max_length=255, unique=True)
    code = models.TextField(blank=True, help_text="Lapis smart-contract YAML.")
    global_state = models.JSONField(default=dict, blank=True, help_text="Runtime Lapis VM state (status, counters, etc.).")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def clean(self):
        if self.code:
            from .lapis.loader import loads_contract
            from .lapis.compiler import ContractFlowValidator
            from .lapis.exceptions import LapisValidationError
            try:
                tree = loads_contract(self.code, fmt="yaml")
                ContractFlowValidator().validate(tree)
            except LapisValidationError as exc:
                raise ValidationError({"code": str(exc)}) from exc


# ---------------------------------------------------------------------------
# Agreement
# ---------------------------------------------------------------------------

class Agreement(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    source_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="agreements_as_source",
    )
    target_account = models.ForeignKey(
        LedgerAccount,
        on_delete=models.PROTECT,
        related_name="agreements_as_target",
    )
    contract = models.ForeignKey(
        Contract,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="agreements",
        help_text="Lapis contract governing this agreement.",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Agreement {self.uuid} ({self.source_account} → {self.target_account})"

    def clean(self):
        if self.source_account_id and self.target_account_id:
            if self.source_account_id == self.target_account_id:
                raise ValidationError("Source and target accounts must differ.")


# ---------------------------------------------------------------------------
# Entitlement
# ---------------------------------------------------------------------------

class EntitlementStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    REVOKED = "revoked", "Revoked"
    EXPIRED = "expired", "Expired"
    SUSPENDED = "suspended", "Suspended"


class EntitlementKind(models.TextChoices):
    SERVICE_ACCESS = "service_access", "Service Access"
    LEASE_RIGHT = "lease_right", "Lease Right"
    VESTING_RIGHT = "vesting_right", "Vesting Right"
    EXERCISE_RIGHT = "exercise_right", "Exercise Right"
    REWARD_ELIGIBILITY = "reward_eligibility", "Reward Eligibility"
    CLAIM_RIGHT = "claim_right", "Claim Right"
    USAGE_RIGHT = "usage_right", "Usage Right"
    OTHER = "other", "Other"


class Entitlement(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        Agreement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="entitlements",
    )
    contract = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.PROTECT,
        related_name="entitlements",
    )
    holder_account = models.ForeignKey(
        LedgerAccount, on_delete=models.PROTECT, related_name="entitlements",
    )
    resource_label = models.CharField(max_length=255)
    kind = models.CharField(max_length=40, choices=EntitlementKind.choices)
    status = models.CharField(
        max_length=20, choices=EntitlementStatus.choices,
        default=EntitlementStatus.ACTIVE,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["holder_account", "status"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.resource_label} ({self.holder_account.code})"


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

class ScheduleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class ScheduleKind(models.TextChoices):
    BILLING = "billing", "Billing"
    RENEWAL = "renewal", "Renewal"
    EXPIRY = "expiry", "Expiry"
    VESTING = "vesting", "Vesting"
    PAYOUT = "payout", "Payout"
    SETTLEMENT = "settlement", "Settlement"
    REWARD = "reward", "Reward"
    REVIEW = "review", "Review"
    CHECKPOINT = "checkpoint", "Checkpoint"
    OTHER = "other", "Other"


class ScheduleQuerySet(models.QuerySet):
    def active(self):
        return self.filter(status=ScheduleStatus.ACTIVE)

    def due(self, now=None):
        from django.utils import timezone as _tz
        if now is None:
            now = _tz.now()
        return self.filter(status=ScheduleStatus.ACTIVE, next_run_at__lte=now)

    def by_kind(self, kind):
        return self.filter(kind=kind)

    def for_source(self, source_type, source_id):
        return self.filter(source_type=source_type, source_id=str(source_id))


class Schedule(models.Model):
    objects = ScheduleQuerySet.as_manager()

    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        Agreement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="schedules",
    )
    contract = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.PROTECT,
        related_name="schedules",
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=30, choices=ScheduleKind.choices)
    status = models.CharField(
        max_length=20, choices=ScheduleStatus.choices,
        default=ScheduleStatus.DRAFT,
    )
    frequency = models.CharField(
        max_length=50, blank=True,
        help_text="once / daily / weekly / monthly / quarterly / yearly / custom",
    )
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(null=True, blank=True)
    next_run_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_run_at", "starts_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status", "next_run_at"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display()})"

    def clean(self):
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})
        has_ref = (
            self.agreement_id
            or self.contract_id
            or (self.source_type and self.source_id)
        )
        if not has_ref and not (self.metadata or {}).get("manual"):
            raise ValidationError(
                "Schedule must reference an agreement, contract, or source_type+source_id "
                "(or set metadata.manual=true)."
            )


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

class ConditionStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    SATISFIED = "satisfied", "Satisfied"
    FAILED = "failed", "Failed"
    WAIVED = "waived", "Waived"
    CANCELLED = "cancelled", "Cancelled"


class ConditionKind(models.TextChoices):
    TIME = "time", "Time"
    STATUS = "status", "Status"
    APPROVAL = "approval", "Approval"
    BALANCE = "balance", "Balance"
    EVIDENCE = "evidence", "Evidence"
    THRESHOLD = "threshold", "Threshold"
    MANUAL = "manual", "Manual"
    EXTERNAL = "external", "External"
    OTHER = "other", "Other"


class Condition(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        Agreement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="conditions",
    )
    contract = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.PROTECT,
        related_name="conditions",
    )
    name = models.CharField(max_length=255)
    kind = models.CharField(max_length=30, choices=ConditionKind.choices)
    status = models.CharField(
        max_length=20, choices=ConditionStatus.choices,
        default=ConditionStatus.PENDING,
    )
    description = models.TextField(blank=True)
    expression = models.JSONField(default=dict, blank=True)
    satisfied_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.name} [{self.get_status_display()}]"

    def clean(self):
        has_ref = (
            self.agreement_id
            or self.contract_id
            or (self.source_type and self.source_id)
        )
        if not has_ref and not (self.metadata or {}).get("manual"):
            raise ValidationError(
                "Condition must reference an agreement, contract, or source_type+source_id "
                "(or set metadata.manual=true)."
            )


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

class AllocationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    RELEASED = "released", "Released"
    CONSUMED = "consumed", "Consumed"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


class AllocationKind(models.TextChoices):
    RESERVE = "reserve", "Reserve"
    ESCROW_HOLD = "escrow_hold", "Escrow Hold"
    COLLATERAL = "collateral", "Collateral"
    MARGIN = "margin", "Margin"
    SPLIT = "split", "Split"
    WATERFALL = "waterfall", "Waterfall"
    STAKING_LOCK = "staking_lock", "Staking Lock"
    VESTING_POOL = "vesting_pool", "Vesting Pool"
    PREPAID_BALANCE = "prepaid_balance", "Prepaid Balance"
    BUDGET = "budget", "Budget"
    OTHER = "other", "Other"


class Allocation(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        Agreement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations",
    )
    contract = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations",
    )
    holder_account = models.ForeignKey(
        LedgerAccount, null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations_as_holder",
    )
    beneficiary_account = models.ForeignKey(
        LedgerAccount, null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations_as_beneficiary",
    )
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="allocations")
    amount_base_units = models.BigIntegerField()
    allocated_amount_base_units = models.BigIntegerField(default=0)
    released_amount_base_units = models.BigIntegerField(default=0)
    consumed_amount_base_units = models.BigIntegerField(default=0)
    kind = models.CharField(max_length=30, choices=AllocationKind.choices)
    status = models.CharField(
        max_length=20, choices=AllocationStatus.choices,
        default=AllocationStatus.DRAFT,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()} {self.amount_base_units} {self.asset.unit_name}"

    def clean(self):
        if self.amount_base_units is not None and self.amount_base_units <= 0:
            raise ValidationError({"amount_base_units": "amount_base_units must be > 0."})
        for fname in ("allocated_amount_base_units", "released_amount_base_units", "consumed_amount_base_units"):
            v = getattr(self, fname)
            if v is not None and v < 0:
                raise ValidationError({fname: f"{fname} cannot be negative."})
        released = self.released_amount_base_units or 0
        consumed = self.consumed_amount_base_units or 0
        total = self.amount_base_units or 0
        if released + consumed > total:
            raise ValidationError("released_amount_base_units + consumed_amount_base_units cannot exceed amount_base_units.")
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})

    @property
    def remaining_amount_base_units(self) -> int:
        return (
            self.amount_base_units
            - self.released_amount_base_units
            - self.consumed_amount_base_units
        )

    @property
    def is_active_now(self) -> bool:
        from django.utils import timezone as _tz
        if self.status != AllocationStatus.ACTIVE:
            return False
        now = _tz.now()
        if self.starts_at and now < self.starts_at:
            return False
        if self.ends_at and now > self.ends_at:
            return False
        return True


# ---------------------------------------------------------------------------
# ContractEvent
# ---------------------------------------------------------------------------

class ContractEventKind(models.TextChoices):
    CREATED = "created", "Created"
    ACTIVATED = "activated", "Activated"
    PAUSED = "paused", "Paused"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"
    RENEWED = "renewed", "Renewed"
    PAYMENT_DUE = "payment_due", "Payment Due"
    PAYMENT_PAID = "payment_paid", "Payment Paid"
    PAYMENT_FAILED = "payment_failed", "Payment Failed"
    ENTITLEMENT_GRANTED = "entitlement_granted", "Entitlement Granted"
    ENTITLEMENT_REVOKED = "entitlement_revoked", "Entitlement Revoked"
    OBLIGATION_CREATED = "obligation_created", "Obligation Created"
    OBLIGATION_FULFILLED = "obligation_fulfilled", "Obligation Fulfilled"
    CONDITION_SATISFIED = "condition_satisfied", "Condition Satisfied"
    CONDITION_FAILED = "condition_failed", "Condition Failed"
    ALLOCATION_CREATED = "allocation_created", "Allocation Created"
    ALLOCATION_RELEASED = "allocation_released", "Allocation Released"
    ALLOCATION_CONSUMED = "allocation_consumed", "Allocation Consumed"
    SETTLEMENT = "settlement", "Settlement"
    DEFAULT = "default", "Default"
    CUSTOM = "custom", "Custom"


class ContractEvent(models.Model):
    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False, db_index=True)
    agreement = models.ForeignKey(
        Agreement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    contract = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    kind = models.CharField(max_length=40, choices=ContractEventKind.choices)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="contract_events",
    )
    transaction = models.ForeignKey(
        LedgerTransaction, null=True, blank=True, on_delete=models.PROTECT,
        related_name="contract_events",
    )
    obligation = models.ForeignKey(
        Obligation, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    entitlement = models.ForeignKey(
        Entitlement, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    schedule = models.ForeignKey(
        Schedule, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    condition = models.ForeignKey(
        Condition, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    allocation = models.ForeignKey(
        Allocation, null=True, blank=True, on_delete=models.PROTECT,
        related_name="events",
    )
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["source_type", "source_id"]),
            models.Index(fields=["kind"]),
        ]

    def __str__(self):
        return f"{self.get_kind_display()}: {self.title}"

    def clean(self):
        has_ref = (
            self.agreement_id
            or self.contract_id
            or self.transaction_id
            or self.obligation_id
            or self.entitlement_id
            or self.schedule_id
            or self.condition_id
            or self.allocation_id
            or (self.source_type and self.source_id)
        )
        if not has_ref and not (self.payload or {}).get("manual"):
            raise ValidationError(
                "ContractEvent must reference at least one object or source, "
                "or set payload.manual=true."
            )
