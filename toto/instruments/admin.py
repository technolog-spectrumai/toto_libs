from django.contrib import admin
from django.urls import reverse
from django.utils.html import format_html

from .models import (
    AmortizationContract,
    AmortizationEntry,
    EscrowContract,
    FinancialInstrument,
    ForwardContract,
    FutureContract,
    FutureMarginPosition,
    FutureMarket,
    InstrumentExecution,
    InstrumentObligation,
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
    list_display = ("reference", "instrument_type", "status", "issuer", "contract_account", "contract_link", "obligation_count", "created_at")
    list_filter = ("instrument_type", "status", "created_at")
    search_fields = ("reference", "issuer__username", "contract_account__code")
    readonly_fields = ("contract_link", "obligation_count")
    inlines = [InstrumentObligationInline, InstrumentExecutionInline]

    @admin.display(description="Contract")
    def contract_link(self, obj):
        c = obj.contract
        if not c:
            return "—"
        url = reverse("admin:assets_contract_change", args=[c.pk])
        return format_html('<a href="{}">{}</a>', url, c.name)

    @admin.display(description="Obligations")
    def obligation_count(self, obj):
        c = obj.contract
        if not c:
            return "—"
        obligations = c.metadata.get("obligations", [])
        return len(obligations)


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
# Amortization admin
# ---------------------------------------------------------------------------

class AmortizationEntryInline(admin.TabularInline):
    model = AmortizationEntry
    extra = 0
    readonly_fields = ["created_at"]
    show_change_link = True


@admin.register(AmortizationContract)
class AmortizationContractAdmin(admin.ModelAdmin):
    list_display = [
        "instrument", "status", "asset",
        "original_amount_base_units", "amortized_amount_base_units",
        "basis", "source_account", "destination_account", "created_at",
    ]
    list_filter = ["status", "asset"]
    search_fields = ["instrument__reference", "source_account__code", "destination_account__code"]
    readonly_fields = ["created_at", "updated_at"]
    inlines = [AmortizationEntryInline]


@admin.register(AmortizationEntry)
class AmortizationEntryAdmin(admin.ModelAdmin):
    list_display = ["contract", "amount_base_units", "source_type", "source_id", "transaction", "created_at"]
    list_filter = ["source_type", "created_at"]
    search_fields = ["contract__instrument__reference", "source_id"]
    readonly_fields = ["created_at"]
