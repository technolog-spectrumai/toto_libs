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
            models.Index(fields=["supervisor"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.real_world_object} tokenized as {self.asset.unit_name}"

    def delete(self, *args, **kwargs):
        raise ValidationError("Tokenization records are permanent and cannot be reverted.")


# ---------------------------------------------------------------------------
# Exchange rates
# ---------------------------------------------------------------------------

class AssetExchangeRate(models.Model):
    from_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="exchange_rates_from",
    )
    to_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="exchange_rates_to",
    )
    rate = models.DecimalField(
        max_digits=24,
        decimal_places=12,
        help_text="Fixed amount of to_asset received for 1 unit of from_asset.",
    )
    commission_percent = models.DecimalField(
        max_digits=7,
        decimal_places=4,
        default=Decimal("0.0000"),
        help_text="Commission charged in the payment asset on top of the converted amount.",
    )
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["from_asset__unit_name", "to_asset__unit_name"]
        constraints = [
            models.UniqueConstraint(
                fields=["from_asset", "to_asset"],
                name="unique_asset_exchange_rate_pair",
            ),
            models.CheckConstraint(
                check=~models.Q(from_asset=models.F("to_asset")),
                name="exchange_rate_distinct_assets",
            ),
        ]
        indexes = [
            models.Index(fields=["from_asset", "to_asset"]),
            models.Index(fields=["active"]),
        ]

    def __str__(self):
        return f"1 {self.from_asset.unit_name} = {self.rate} {self.to_asset.unit_name}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        AssetExchangeRateHistory.objects.create(
            exchange_rate=self,
            from_asset=self.from_asset,
            to_asset=self.to_asset,
            rate=self.rate,
            commission_percent=self.commission_percent,
            metadata={"active": self.active, **(self.metadata or {})},
        )

    def clean(self):
        if self.rate is not None and self.rate <= 0:
            raise ValidationError({"rate": "Exchange rate must be positive."})
        if self.commission_percent is not None and self.commission_percent < 0:
            raise ValidationError({"commission_percent": "Commission cannot be negative."})
        if self.from_asset_id and self.from_asset_id == self.to_asset_id:
            raise ValidationError("Exchange rate assets must be different.")


class AssetExchangeRateHistory(models.Model):
    exchange_rate = models.ForeignKey(
        AssetExchangeRate,
        on_delete=models.CASCADE,
        related_name="history",
    )
    from_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="exchange_rate_history_from",
    )
    to_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="exchange_rate_history_to",
    )
    rate = models.DecimalField(max_digits=24, decimal_places=12)
    commission_percent = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("0.0000"))
    recorded_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["recorded_at"]
        verbose_name_plural = "asset exchange rate history"
        indexes = [
            models.Index(fields=["from_asset", "to_asset", "recorded_at"]),
            models.Index(fields=["exchange_rate", "recorded_at"]),
        ]

    def __str__(self):
        return f"{self.recorded_at:%Y-%m-%d %H:%M} · 1 {self.from_asset.unit_name} = {self.rate} {self.to_asset.unit_name}"


class ExchangeRequestStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    ACCEPTED = "accepted", "Accepted"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class AssetExchangeRequest(models.Model):
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="asset_exchange_requests_made",
    )
    counterparty = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="asset_exchange_requests_received",
    )
    requester_account = models.ForeignKey(
        "LedgerAccount",
        on_delete=models.PROTECT,
        related_name="exchange_requests_made",
    )
    counterparty_account = models.ForeignKey(
        "LedgerAccount",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="exchange_requests_received",
    )
    offer_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="exchange_requests_offered",
    )
    request_asset = models.ForeignKey(
        Asset,
        on_delete=models.PROTECT,
        related_name="exchange_requests_requested",
    )
    offer_amount_base_units = models.BigIntegerField()
    request_amount_base_units = models.BigIntegerField()
    commission_amount_base_units = models.BigIntegerField(default=0)
    exchange_rate = models.DecimalField(max_digits=24, decimal_places=12)
    commission_percent = models.DecimalField(max_digits=7, decimal_places=4, default=Decimal("0.0000"))
    status = models.CharField(
        max_length=20,
        choices=ExchangeRequestStatus.choices,
        default=ExchangeRequestStatus.PENDING,
    )
    offer_tx_reference = models.CharField(max_length=255, blank=True)
    request_tx_reference = models.CharField(max_length=255, blank=True)
    commission_tx_reference = models.CharField(max_length=255, blank=True)
    note = models.TextField(blank=True)
    response_note = models.TextField(blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["requester", "status"]),
            models.Index(fields=["counterparty", "status"]),
            models.Index(fields=["offer_asset", "request_asset"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return (
            f"{self.requester} offers {self.offer_amount_display} {self.offer_asset.unit_name} "
            f"for {self.request_amount_display} {self.request_asset.unit_name}"
        )

    @property
    def offer_amount_display(self) -> Decimal:
        return from_base_units(self.offer_amount_base_units, self.offer_asset.decimals)

    @property
    def request_amount_display(self) -> Decimal:
        return from_base_units(self.request_amount_base_units, self.request_asset.decimals)

    @property
    def commission_amount_display(self) -> Decimal:
        return from_base_units(self.commission_amount_base_units, self.offer_asset.decimals)

    @property
    def requester_total_display(self) -> Decimal:
        return self.offer_amount_display + self.commission_amount_display


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

class ObligationStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    OVERDUE = "overdue", "Overdue"
    FULFILLED = "fulfilled", "Fulfilled"
    DEFAULTED = "defaulted", "Defaulted"


class Obligation(models.Model):
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
