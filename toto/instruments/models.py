from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from toto.assets.models import from_base_units


class InstrumentStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    SETTLED = "settled", "Settled"
    DEFAULTED = "defaulted", "Defaulted"
    CANCELLED = "cancelled", "Cancelled"
    EXPIRED = "expired", "Expired"


class InstrumentType(models.TextChoices):
    ESCROW = "escrow", "Escrow"
    FORWARD = "forward", "Forward"
    FUTURE = "future", "Future"
    OPTION = "option", "Option"
    REVENUE_SHARE = "revenue_share", "Revenue Share"
    SUBSCRIPTION = "subscription", "Subscription"
    VESTING = "vesting", "Vesting"
    STAKING = "staking", "Staking"
    LEASE = "lease", "Lease"
    AMORTIZATION = "amortization", "Amortization"


class FinancialInstrument(models.Model):
    reference = models.CharField(max_length=255, unique=True)
    instrument_type = models.CharField(max_length=40, choices=InstrumentType.choices)
    issuer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="issued_instruments",
    )
    status = models.CharField(
        max_length=20,
        choices=InstrumentStatus.choices,
        default=InstrumentStatus.DRAFT,
    )
    contract_account = models.ForeignKey(
        "assets.LedgerAccount",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="financial_instruments",
        help_text="Contract/vault/margin account used by this instrument, if applicable.",
    )
    contract = models.OneToOneField(
        "assets.Contract",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="financial_instrument",
        help_text="Lapis smart-contract generated from this instrument's parameters.",
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    terms = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["reference"]),
            models.Index(fields=["instrument_type", "status"]),
            models.Index(fields=["issuer", "status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.reference} ({self.get_instrument_type_display()})"


class InstrumentObligationRole(models.TextChoices):
    UNDERLYING_DELIVERY = "underlying_delivery", "Underlying Delivery"
    PAYMENT = "payment", "Payment"
    MARGIN = "margin", "Margin"
    SETTLEMENT = "settlement", "Settlement"
    PREMIUM = "premium", "Premium"
    COLLATERAL_RETURN = "collateral_return", "Collateral Return"
    PAYOUT = "payout", "Payout"
    REWARD = "reward", "Reward"
    ESCROW_RELEASE = "escrow_release", "Escrow Release"
    ESCROW_REFUND = "escrow_refund", "Escrow Refund"


class InstrumentObligation(models.Model):
    instrument = models.ForeignKey(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="obligation_links",
    )
    obligation = models.OneToOneField(
        "assets.Obligation",
        on_delete=models.PROTECT,
        related_name="instrument_link",
    )
    role = models.CharField(max_length=40, choices=InstrumentObligationRole.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("instrument", "role")]
        ordering = ["instrument", "role"]
        indexes = [models.Index(fields=["role"])]

    def __str__(self):
        return f"{self.instrument.reference} / {self.role} / {self.obligation.reference}"


class InstrumentExecutionStatus(models.TextChoices):
    SUCCESS = "success", "Success"
    FAILED = "failed", "Failed"


class InstrumentExecution(models.Model):
    instrument = models.ForeignKey(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="executions",
    )
    action = models.CharField(max_length=100)
    input_data = models.JSONField(default=dict, blank=True)
    result_data = models.JSONField(default=dict, blank=True)
    transaction = models.ForeignKey(
        "assets.LedgerTransaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="instrument_executions",
    )
    status = models.CharField(max_length=20, choices=InstrumentExecutionStatus.choices)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["instrument", "action"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self):
        return f"{self.instrument.reference}: {self.action} ({self.status})"


class EscrowStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    FUNDED = "funded", "Funded"
    RELEASED = "released", "Released"
    REFUNDED = "refunded", "Refunded"
    DISPUTED = "disputed", "Disputed"
    CANCELLED = "cancelled", "Cancelled"


class EscrowContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="escrow_contract",
    )
    buyer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="escrows_as_buyer",
    )
    seller_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="escrows_as_seller",
    )
    escrow_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="escrow_contracts",
        help_text="Contract account holding the escrowed asset.",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="escrow_contracts",
    )
    amount_base_units = models.BigIntegerField()
    status = models.CharField(max_length=20, choices=EscrowStatus.choices, default=EscrowStatus.DRAFT)
    order_reference = models.CharField(max_length=255, blank=True)
    release_condition = models.JSONField(default=dict, blank=True)
    funded_at = models.DateTimeField(null=True, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    refunded_at = models.DateTimeField(null=True, blank=True)
    disputed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["buyer_account", "status"]),
            models.Index(fields=["seller_account", "status"]),
            models.Index(fields=["asset", "status"]),
            models.Index(fields=["order_reference"]),
        ]

    def __str__(self):
        return f"{self.instrument.reference}: {self.amount_display} {self.asset.unit_name} escrow"

    def clean(self):
        if self.amount_base_units is not None and self.amount_base_units <= 0:
            raise ValidationError({"amount_base_units": "Escrow amount must be positive."})
        if self.buyer_account_id and self.escrow_account_id and self.buyer_account_id == self.escrow_account_id:
            raise ValidationError("Buyer account and escrow account must be different.")
        if self.seller_account_id and self.escrow_account_id and self.seller_account_id == self.escrow_account_id:
            raise ValidationError("Seller account and escrow account must be different.")

    @property
    def amount_display(self):
        return from_base_units(self.amount_base_units, self.asset.decimals)


class ForwardSettlementType(models.TextChoices):
    PHYSICAL = "physical", "Physical Delivery"
    CASH = "cash", "Cash Settlement"


class ForwardContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="forward_contract",
    )
    buyer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="forward_contracts_as_buyer",
    )
    seller_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="forward_contracts_as_seller",
    )
    underlying_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="forward_contracts_as_underlying",
    )
    quantity_base_units = models.BigIntegerField()
    payment_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="forward_contracts_as_payment",
    )
    payment_amount_base_units = models.BigIntegerField()
    settlement_type = models.CharField(
        max_length=20,
        choices=ForwardSettlementType.choices,
        default=ForwardSettlementType.PHYSICAL,
    )
    settlement_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-settlement_at"]
        indexes = [
            models.Index(fields=["buyer_account", "settlement_at"]),
            models.Index(fields=["seller_account", "settlement_at"]),
            models.Index(fields=["underlying_asset", "settlement_at"]),
        ]

    def clean(self):
        if self.quantity_base_units is not None and self.quantity_base_units <= 0:
            raise ValidationError({"quantity_base_units": "Quantity must be positive."})
        if self.payment_amount_base_units is not None and self.payment_amount_base_units <= 0:
            raise ValidationError({"payment_amount_base_units": "Payment amount must be positive."})

    @property
    def quantity_display(self):
        return from_base_units(self.quantity_base_units, self.underlying_asset.decimals)

    @property
    def payment_amount_display(self):
        return from_base_units(self.payment_amount_base_units, self.payment_asset.decimals)


class FutureSettlementType(models.TextChoices):
    CASH = "cash", "Cash Settlement"
    PHYSICAL = "physical", "Physical Delivery"


class FutureMarket(models.Model):
    code = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=255, blank=True)
    underlying_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="future_markets_as_underlying",
    )
    contract_size_base_units = models.BigIntegerField()
    settlement_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="future_markets_as_settlement_asset",
    )
    settlement_type = models.CharField(
        max_length=20,
        choices=FutureSettlementType.choices,
        default=FutureSettlementType.CASH,
    )
    initial_margin_bps = models.PositiveIntegerField(default=1000)
    maintenance_margin_bps = models.PositiveIntegerField(default=750)
    active = models.BooleanField(default=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["underlying_asset", "active"]),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        if self.contract_size_base_units is not None and self.contract_size_base_units <= 0:
            raise ValidationError({"contract_size_base_units": "Contract size must be positive."})
        if self.maintenance_margin_bps > self.initial_margin_bps:
            raise ValidationError({"maintenance_margin_bps": "Maintenance margin cannot exceed initial margin."})

    @property
    def contract_size_display(self):
        return from_base_units(self.contract_size_base_units, self.underlying_asset.decimals)


class FutureContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="future_contract",
    )
    market = models.ForeignKey(
        FutureMarket,
        on_delete=models.PROTECT,
        related_name="contracts",
    )
    long_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="future_long_positions",
    )
    short_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="future_short_positions",
    )
    contract_count = models.PositiveIntegerField(default=1)
    entry_price_base_units = models.BigIntegerField()
    settlement_price_base_units = models.BigIntegerField(null=True, blank=True)
    settlement_at = models.DateTimeField()
    opened_at = models.DateTimeField(null=True, blank=True)
    settled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-settlement_at"]
        indexes = [
            models.Index(fields=["market", "settlement_at"]),
            models.Index(fields=["long_account", "settlement_at"]),
            models.Index(fields=["short_account", "settlement_at"]),
        ]

    def clean(self):
        if self.entry_price_base_units is not None and self.entry_price_base_units <= 0:
            raise ValidationError({"entry_price_base_units": "Entry price must be positive."})
        if self.long_account_id and self.short_account_id and self.long_account_id == self.short_account_id:
            raise ValidationError("Long and short accounts must be different.")

    @property
    def notional_base_units(self):
        return self.market.contract_size_base_units * self.contract_count * self.entry_price_base_units


class FutureMarginPosition(models.Model):
    future = models.ForeignKey(
        FutureContract,
        on_delete=models.PROTECT,
        related_name="margin_positions",
    )
    account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="future_margin_positions",
    )
    margin_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="future_margin_pool_positions",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="future_margin_positions",
    )
    required_margin_base_units = models.BigIntegerField()
    deposited_margin_base_units = models.BigIntegerField(default=0)
    margin_call_active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("future", "account")]
        ordering = ["future", "account"]

    def clean(self):
        if self.required_margin_base_units is not None and self.required_margin_base_units < 0:
            raise ValidationError({"required_margin_base_units": "Required margin cannot be negative."})
        if self.deposited_margin_base_units is not None and self.deposited_margin_base_units < 0:
            raise ValidationError({"deposited_margin_base_units": "Deposited margin cannot be negative."})

    @property
    def required_margin_display(self):
        return from_base_units(self.required_margin_base_units, self.asset.decimals)

    @property
    def deposited_margin_display(self):
        return from_base_units(self.deposited_margin_base_units, self.asset.decimals)


class RevenueShareContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="revenue_share_contract",
    )
    revenue_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="revenue_share_sources",
    )
    revenue_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="revenue_share_contracts",
    )
    active_from = models.DateTimeField()
    active_until = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.active_until and self.active_until <= self.active_from:
            raise ValidationError({"active_until": "Active-until must be after active-from."})


class RevenueShareRecipient(models.Model):
    contract = models.ForeignKey(
        RevenueShareContract,
        on_delete=models.PROTECT,
        related_name="recipients",
    )
    account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="revenue_share_entitlements",
    )
    share_bps = models.PositiveIntegerField(help_text="10000 bps = 100%")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("contract", "account")]

    def clean(self):
        if self.share_bps > 10000:
            raise ValidationError({"share_bps": "Share cannot exceed 10000 bps."})



class VestingContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="vesting_contract",
    )
    grantor_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="vesting_grants_made",
    )
    beneficiary_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="vesting_grants_received",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="vesting_contracts",
    )
    total_amount_base_units = models.BigIntegerField()
    released_amount_base_units = models.BigIntegerField(default=0)
    start_at = models.DateTimeField()
    cliff_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField()
    release_frequency = models.CharField(max_length=20, default="monthly")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.total_amount_base_units is not None and self.total_amount_base_units <= 0:
            raise ValidationError({"total_amount_base_units": "Total amount must be positive."})
        if self.released_amount_base_units > self.total_amount_base_units:
            raise ValidationError({"released_amount_base_units": "Released amount cannot exceed total amount."})
        if self.end_at <= self.start_at:
            raise ValidationError({"end_at": "End must be after start."})
        if self.cliff_at and not (self.start_at <= self.cliff_at <= self.end_at):
            raise ValidationError({"cliff_at": "Cliff must be inside vesting period."})

    @property
    def remaining_amount_base_units(self):
        return self.total_amount_base_units - self.released_amount_base_units


class StakingPosition(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="staking_position",
    )
    staker_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="staking_positions",
    )
    staking_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="staking_pool_positions",
    )
    staked_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="staking_positions",
    )
    staked_amount_base_units = models.BigIntegerField()
    reward_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="staking_reward_positions",
    )
    reward_rate_bps = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    unstaked_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.staked_amount_base_units is not None and self.staked_amount_base_units <= 0:
            raise ValidationError({"staked_amount_base_units": "Staked amount must be positive."})

    @property
    def staked_amount_display(self):
        return from_base_units(self.staked_amount_base_units, self.staked_asset.decimals)


class OptionType(models.TextChoices):
    CALL = "call", "Call"
    PUT = "put", "Put"


class OptionStyle(models.TextChoices):
    EUROPEAN = "european", "European"
    AMERICAN = "american", "American"


class OptionSettlementType(models.TextChoices):
    PHYSICAL = "physical", "Physical Delivery"
    CASH = "cash", "Cash Settlement"


class OptionContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="option_contract",
    )
    option_type = models.CharField(max_length=10, choices=OptionType.choices)
    style = models.CharField(max_length=10, choices=OptionStyle.choices, default=OptionStyle.EUROPEAN)
    buyer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="option_positions_as_buyer",
        help_text="Holds the right to exercise.",
    )
    writer_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="option_positions_as_writer",
        help_text="Has the obligation if exercised.",
    )
    underlying_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="option_contracts_as_underlying",
    )
    quantity_base_units = models.BigIntegerField()
    payment_asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="option_contracts_as_payment",
        help_text="Asset used to pay the strike price.",
    )
    strike_price_base_units = models.BigIntegerField(
        help_text="Strike price per unit of underlying, in payment asset base units.",
    )
    premium_base_units = models.BigIntegerField(
        default=0,
        help_text="Premium paid by buyer to writer for the option.",
    )
    settlement_type = models.CharField(
        max_length=20,
        choices=OptionSettlementType.choices,
        default=OptionSettlementType.CASH,
    )
    expiry_at = models.DateTimeField()
    exercised_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-expiry_at"]
        indexes = [
            models.Index(fields=["buyer_account", "expiry_at"]),
            models.Index(fields=["writer_account", "expiry_at"]),
            models.Index(fields=["underlying_asset", "option_type", "expiry_at"]),
        ]

    def __str__(self):
        return f"{self.instrument.reference}: {self.get_option_type_display()} {self.get_style_display()}"

    def clean(self):
        if self.quantity_base_units is not None and self.quantity_base_units <= 0:
            raise ValidationError({"quantity_base_units": "Quantity must be positive."})
        if self.strike_price_base_units is not None and self.strike_price_base_units <= 0:
            raise ValidationError({"strike_price_base_units": "Strike price must be positive."})
        if self.premium_base_units is not None and self.premium_base_units < 0:
            raise ValidationError({"premium_base_units": "Premium cannot be negative."})
        if self.buyer_account_id and self.writer_account_id and self.buyer_account_id == self.writer_account_id:
            raise ValidationError("Buyer and writer accounts must be different.")

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() > self.expiry_at

    @property
    def is_exercised(self):
        return self.exercised_at is not None

    @property
    def quantity_display(self):
        return from_base_units(self.quantity_base_units, self.underlying_asset.decimals)

    @property
    def strike_price_display(self):
        return from_base_units(self.strike_price_base_units, self.payment_asset.decimals)

    @property
    def premium_display(self):
        return from_base_units(self.premium_base_units, self.payment_asset.decimals)


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------

class BillingCycle(models.TextChoices):
    DAILY = "daily", "Daily"
    WEEKLY = "weekly", "Weekly"
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    YEARLY = "yearly", "Yearly"




# ---------------------------------------------------------------------------
# Amortization
# ---------------------------------------------------------------------------

class AmortizationStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    ACTIVE = "active", "Active"
    PAUSED = "paused", "Paused"
    EXHAUSTED = "exhausted", "Exhausted"
    CANCELLED = "cancelled", "Cancelled"


class AmortizationContract(models.Model):
    instrument = models.OneToOneField(
        FinancialInstrument,
        on_delete=models.PROTECT,
        related_name="amortization_contract",
    )
    source_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="amortization_sources",
    )
    destination_account = models.ForeignKey(
        "assets.LedgerAccount",
        on_delete=models.PROTECT,
        related_name="amortization_destinations",
    )
    asset = models.ForeignKey(
        "assets.Asset",
        on_delete=models.PROTECT,
        related_name="amortization_contracts",
    )
    original_amount_base_units = models.BigIntegerField()
    amortized_amount_base_units = models.BigIntegerField(default=0)
    basis = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=20,
        choices=AmortizationStatus.choices,
        default=AmortizationStatus.DRAFT,
    )
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["source_account", "status"]),
            models.Index(fields=["destination_account", "status"]),
            models.Index(fields=["asset", "status"]),
        ]

    def __str__(self):
        return f"{self.instrument.reference} (amortization)"

    def clean(self):
        if self.original_amount_base_units is not None and self.original_amount_base_units <= 0:
            raise ValidationError({"original_amount_base_units": "Original amount must be positive."})
        if self.amortized_amount_base_units is not None and self.amortized_amount_base_units < 0:
            raise ValidationError({"amortized_amount_base_units": "Amortized amount cannot be negative."})
        if (
            self.original_amount_base_units is not None
            and self.amortized_amount_base_units is not None
            and self.amortized_amount_base_units > self.original_amount_base_units
        ):
            raise ValidationError({"amortized_amount_base_units": "Amortized amount cannot exceed original amount."})
        if (
            self.source_account_id
            and self.destination_account_id
            and self.source_account_id == self.destination_account_id
        ):
            raise ValidationError("Source and destination accounts must differ.")
        if self.ends_at and self.starts_at and self.ends_at <= self.starts_at:
            raise ValidationError({"ends_at": "ends_at must be after starts_at."})

    @property
    def remaining_amount_base_units(self):
        return self.original_amount_base_units - self.amortized_amount_base_units

    @property
    def is_exhausted(self):
        return self.remaining_amount_base_units == 0


class AmortizationEntry(models.Model):
    contract = models.ForeignKey(
        AmortizationContract,
        on_delete=models.PROTECT,
        related_name="entries",
    )
    amount_base_units = models.BigIntegerField()
    source_type = models.CharField(max_length=100, blank=True)
    source_id = models.CharField(max_length=255, blank=True)
    transaction = models.ForeignKey(
        "assets.LedgerTransaction",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="amortization_entries",
    )
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["contract", "created_at"]),
        ]

    def __str__(self):
        return f"{self.contract.instrument.reference}: -{self.amount_base_units} base units"

    def clean(self):
        if self.amount_base_units is not None and self.amount_base_units <= 0:
            raise ValidationError({"amount_base_units": "Amount must be positive."})
        if (
            self.amount_base_units is not None
            and self.contract_id is not None
            and self.amount_base_units > self.contract.remaining_amount_base_units
        ):
            raise ValidationError(
                {"amount_base_units": "Amount exceeds the contract's remaining balance."}
            )
