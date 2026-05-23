from django.contrib import admin

from .models import (
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    FutureContract,
    FutureMarginPosition,
    FutureMarket,
    InstrumentExecution,
    InstrumentObligation,
    LeaseCharge,
    LeaseContract,
    LeaseMetric,
    LeaseTariff,
    OptionContract,
    RevenueShareContract,
    RevenueShareRecipient,
    StakingPosition,
    VestingContract,
)


class InstrumentObligationInline(admin.TabularInline):
    model = InstrumentObligation
    extra = 0
    readonly_fields = ("created_at",)


class InstrumentExecutionInline(admin.TabularInline):
    model = InstrumentExecution
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(FinancialInstrument)
class FinancialInstrumentAdmin(admin.ModelAdmin):
    list_display = ("reference", "instrument_type", "status", "issuer", "contract_account", "created_at")
    list_filter = ("instrument_type", "status", "created_at")
    search_fields = ("reference", "issuer__username", "contract_account__code")
    inlines = [InstrumentObligationInline, InstrumentExecutionInline]


@admin.register(EscrowContract)
class EscrowContractAdmin(admin.ModelAdmin):
    list_display = ("instrument", "status", "buyer_account", "seller_account", "asset", "amount_base_units", "order_reference")
    list_filter = ("status", "asset", "created_at")
    search_fields = ("instrument__reference", "order_reference", "buyer_account__code", "seller_account__code")


@admin.register(ForwardContract)
class ForwardContractAdmin(admin.ModelAdmin):
    list_display = ("instrument", "buyer_account", "seller_account", "underlying_asset", "quantity_base_units", "payment_asset", "settlement_at")
    list_filter = ("settlement_type", "underlying_asset", "payment_asset", "settlement_at")
    search_fields = ("instrument__reference", "buyer_account__code", "seller_account__code")


@admin.register(FutureMarket)
class FutureMarketAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "underlying_asset", "contract_size_base_units", "settlement_asset", "settlement_type", "active")
    list_filter = ("settlement_type", "active", "underlying_asset", "settlement_asset")
    search_fields = ("code", "name")


@admin.register(FutureContract)
class FutureContractAdmin(admin.ModelAdmin):
    list_display = ("instrument", "market", "long_account", "short_account", "contract_count", "entry_price_base_units", "settlement_at")
    list_filter = ("market", "settlement_at")
    search_fields = ("instrument__reference", "long_account__code", "short_account__code")


@admin.register(FutureMarginPosition)
class FutureMarginPositionAdmin(admin.ModelAdmin):
    list_display = ("future", "account", "asset", "required_margin_base_units", "deposited_margin_base_units", "margin_call_active")
    list_filter = ("margin_call_active", "asset")
    search_fields = ("future__instrument__reference", "account__code")


class RevenueShareRecipientInline(admin.TabularInline):
    model = RevenueShareRecipient
    extra = 0


@admin.register(RevenueShareContract)
class RevenueShareContractAdmin(admin.ModelAdmin):
    list_display = ("instrument", "revenue_account", "revenue_asset", "active_from", "active_until")
    list_filter = ("revenue_asset", "active_from")
    search_fields = ("instrument__reference", "revenue_account__code")
    inlines = [RevenueShareRecipientInline]


admin.site.register(OptionContract)
admin.site.register(VestingContract)
admin.site.register(StakingPosition)
admin.site.register(InstrumentObligation)
admin.site.register(InstrumentExecution)


# ---------------------------------------------------------------------------
# Lease admin
# ---------------------------------------------------------------------------

class LeaseTariffInline(admin.TabularInline):
    model = LeaseTariff
    extra = 0
    readonly_fields = ["created_at", "updated_at"]


class LeaseChargeInline(admin.TabularInline):
    model = LeaseCharge
    extra = 0
    readonly_fields = ["created_at", "charged_at"]
    show_change_link = True


@admin.register(LeaseContract)
class LeaseContractAdmin(admin.ModelAdmin):
    list_display = [
        "instrument", "billing_mode", "status", "lessee_account",
        "lessor_account", "next_billing_at", "created_at",
    ]
    list_filter = ["status", "billing_mode", "billing_period"]
    search_fields = ["instrument__reference"]
    readonly_fields = ["created_at", "updated_at", "activated_at", "cancelled_at"]
    inlines = [LeaseTariffInline, LeaseChargeInline]


@admin.register(LeaseMetric)
class LeaseMetricAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "kind", "unit", "step", "active"]
    list_filter = ["kind", "active"]
    search_fields = ["code", "name"]


@admin.register(LeaseTariff)
class LeaseTariffAdmin(admin.ModelAdmin):
    list_display = ["lease", "metric", "price_per_step_base_units", "rounding_mode", "active"]
    list_filter = ["rounding_mode", "active"]
    readonly_fields = ["created_at", "updated_at"]


@admin.register(LeaseCharge)
class LeaseChargeAdmin(admin.ModelAdmin):
    list_display = ["lease", "metric", "raw_quantity", "amount_base_units", "status", "charged_at"]
    list_filter = ["status"]
    search_fields = ["lease__instrument__reference"]
    readonly_fields = ["created_at", "charged_at"]
