from django.contrib import admin

from .hashing import verify_hash_chain
from .models import (
    Asset,
    AssetHolding,
    LedgerAccount,
    LedgerAccountKey,
    LedgerAuthorization,
    LedgerEntry,
    LedgerEntryComment,
    LedgerEntryTag,
    LedgerHash,
    LedgerTag,
    LedgerTransaction,
    WalletAuthorization,
    WalletPin,
)


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
    list_display = ("name", "unit_name", "decimals", "max_supply_display", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "unit_name")
    readonly_fields = ("created_at", "updated_at", "max_supply_display")
    inlines = [AssetHoldingInline]

    @staticmethod
    def max_supply_display(obj):
        return f"{obj.max_supply_display} {obj.unit_name}"
    max_supply_display.short_description = "Total supply"


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


@admin.register(LedgerAccountKey)
class LedgerAccountKeyAdmin(admin.ModelAdmin):
    list_display = ("key_id", "ledger_account", "algorithm", "state", "valid_from", "valid_until", "created_at")
    list_filter = ("state", "algorithm")
    search_fields = ("key_id", "ledger_account__code")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("ledger_account", "encrypted_private_key")
    fieldsets = (
        (None, {"fields": ("ledger_account", "encrypted_private_key", "key_id", "algorithm", "state")}),
        ("Validity", {"fields": ("valid_from", "valid_until")}),
        ("Public key snapshot", {"fields": ("public_key_pem",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(LedgerAuthorization)
class LedgerAuthorizationAdmin(admin.ModelAdmin):
    list_display = ("ledger_account", "delegate_user", "scopes_display", "asset", "valid_from", "valid_until", "revoked_at", "created_at")
    list_filter = ("asset",)
    search_fields = ("ledger_account__code", "delegate_user__username")
    readonly_fields = ("created_at", "updated_at", "signed_grant_payload", "grant_signature")
    raw_id_fields = ("ledger_account", "delegate_user", "delegate_key", "asset", "signed_by_account_key")
    fieldsets = (
        (None, {"fields": ("ledger_account", "delegate_user", "delegate_key", "scopes", "asset", "max_amount_base_units")}),
        ("Validity", {"fields": ("valid_from", "valid_until", "revoked_at")}),
        ("Grant signature", {"fields": ("signed_by_account_key", "signed_grant_payload", "grant_signature"), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )

    def scopes_display(self, obj):
        return ", ".join(obj.scopes or []) or "—"
    scopes_display.short_description = "Scopes"


@admin.register(WalletAuthorization)
class WalletAuthorizationAdmin(admin.ModelAdmin):
    list_display = ("name", "ledger_account", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "ledger_account__code")
    readonly_fields = ("created_at", "updated_at", "private_key_encrypted")
    autocomplete_fields = ["ledger_account"]
    fieldsets = (
        (None, {"fields": ("name", "ledger_account", "active")}),
        ("Keys", {
            "fields": ("public_key", "private_key_encrypted"),
            "description": "Private key is stored encrypted. Use set_private_key() programmatically.",
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(WalletPin)
class WalletPinAdmin(admin.ModelAdmin):
    list_display = ("user", "created_at", "updated_at")
    search_fields = ("user__username",)
    readonly_fields = ("user", "secret", "created_at", "updated_at")

    def has_add_permission(self, request):
        return False


# Metering — the shared bases live in toto.quota.admin so every app's limits
# screen looks the same.
from toto.quota.admin import QuotaPolicyAdminBase, UsageEventAdminBase
from .models import AssetsQuotaPolicy, AssetsUsageEvent


@admin.register(AssetsQuotaPolicy)
class AssetsQuotaPolicyAdmin(QuotaPolicyAdminBase):
    pass


@admin.register(AssetsUsageEvent)
class AssetsUsageEventAdmin(UsageEventAdminBase):
    pass


# ---------------------------------------------------------------------------
# Ledger decorations
# ---------------------------------------------------------------------------
#
# Ordinary editable rows, unlike everything above them: they are notes ABOUT the
# ledger and were never part of it. `entry_id` shows as a raw integer because it
# is deliberately not a ForeignKey — see LedgerEntryTag's docstring.


@admin.register(LedgerTag)
class LedgerTagAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "slug", "created_at")
    list_filter = ("account",)
    search_fields = ("name", "slug", "account__code")
    raw_id_fields = ("account",)


@admin.register(LedgerEntryTag)
class LedgerEntryTagAdmin(admin.ModelAdmin):
    list_display = ("entry_id", "tag", "created_by", "created_at")
    search_fields = ("entry_id", "tag__name")
    raw_id_fields = ("tag", "created_by")


@admin.register(LedgerEntryComment)
class LedgerEntryCommentAdmin(admin.ModelAdmin):
    list_display = ("entry_id", "body", "author", "updated_at")
    search_fields = ("entry_id", "body")
    raw_id_fields = ("author",)
