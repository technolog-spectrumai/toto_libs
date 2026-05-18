from django.contrib import admin

from .hashing import verify_hash_chain
from .models import Asset, AssetHolding, LedgerAccount, LedgerEntry, LedgerHash, LedgerTransaction


class AssetHoldingInline(admin.TabularInline):
    model = AssetHolding
    extra = 0
    readonly_fields = ("asset", "account", "balance_base_units", "balance_display", "created_at", "updated_at")
    fields = ("account", "balance_base_units", "balance_display", "updated_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def balance_display(obj):
        return obj.balance_display


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "unit_name", "decimals", "total_supply_display", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "unit_name")
    readonly_fields = ("created_at", "updated_at", "total_supply_display")
    inlines = [AssetHoldingInline]

    @staticmethod
    def total_supply_display(obj):
        return f"{obj.total_supply_display} {obj.unit_name}"
    total_supply_display.short_description = "Total supply"


@admin.register(LedgerAccount)
class LedgerAccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "active", "user", "created_at")
    list_filter = ("account_type", "active")
    search_fields = ("code", "name")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("user",)


@admin.register(AssetHolding)
class AssetHoldingAdmin(admin.ModelAdmin):
    list_display = ("account", "asset", "balance_base_units", "balance_display", "updated_at")
    list_filter = ("asset",)
    search_fields = ("account__code", "asset__unit_name")
    readonly_fields = ("created_at", "updated_at", "balance_display")
    raw_id_fields = ("asset", "account")

    @staticmethod
    def balance_display(obj):
        return obj.balance_display
    balance_display.short_description = "Balance (display)"


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    readonly_fields = ("account", "asset", "amount_base_units", "amount_display", "created_at")
    fields = ("account", "asset", "amount_base_units", "amount_display", "created_at")

    def has_add_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def amount_display(obj):
        return obj.amount_display


@admin.register(LedgerTransaction)
class LedgerTransactionAdmin(admin.ModelAdmin):
    list_display = (
        "reference", "transaction_type", "asset", "posted", "reversed_transaction", "created_at"
    )
    list_filter = ("transaction_type", "posted", "asset")
    search_fields = ("reference", "description")
    readonly_fields = ("created_at", "posted")
    raw_id_fields = ("asset", "reversed_transaction")
    inlines = [LedgerEntryInline]

    def has_change_permission(self, request, obj=None):
        if obj and obj.posted:
            return False
        return super().has_change_permission(request, obj)


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("transaction", "account", "asset", "amount_base_units", "amount_display", "created_at")
    list_filter = ("asset",)
    search_fields = ("transaction__reference", "account__code")
    readonly_fields = ("transaction", "account", "asset", "amount_base_units", "amount_display", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def amount_display(obj):
        return obj.amount_display


@admin.register(LedgerHash)
class LedgerHashAdmin(admin.ModelAdmin):
    list_display = ("transaction", "hash_short", "previous_hash_short", "chain_valid", "created_at")
    readonly_fields = ("transaction", "previous_hash", "hash", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @staticmethod
    def hash_short(obj):
        return obj.hash[:16] + "…"
    hash_short.short_description = "Hash"

    @staticmethod
    def previous_hash_short(obj):
        return (obj.previous_hash[:16] + "…") if obj.previous_hash else "—"
    previous_hash_short.short_description = "Previous hash"

    def chain_valid(self, obj):
        return verify_hash_chain()
    chain_valid.boolean = True
    chain_valid.short_description = "Chain valid"
