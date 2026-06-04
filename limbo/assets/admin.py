from django import forms as django_forms
from django.contrib import admin
from django.utils.safestring import mark_safe

from .hashing import verify_hash_chain
from .models import (
    Agreement,
    Asset,
    AssetHolding,
    Contract,
    Currency,
    LedgerAccount,
    LedgerAccountKey,
    LedgerAuthorization,
    LedgerEntry,
    LedgerHash,
    LedgerTransaction,
    Obligation,
    Tokenization,
    WalletAuthorization,
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


class TokenizationInline(admin.TabularInline):
    model = Tokenization
    extra = 0
    readonly_fields = ("created_at", "status", "default_reason", "default_note", "defaulted_at", "defaulted_by")
    raw_id_fields = ("real_world_object", "supervisor", "defaulted_by")
    fields = ("real_world_object", "supervisor", "status", "default_reason", "defaulted_at", "defaulted_by", "created_at", "metadata")

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "unit_name", "decimals", "total_supply_display", "active", "created_at")
    list_filter = ("active",)
    search_fields = ("name", "unit_name")
    readonly_fields = ("created_at", "updated_at", "total_supply_display")
    inlines = [AssetHoldingInline, TokenizationInline]

    @staticmethod
    def total_supply_display(obj):
        return f"{obj.total_supply_display} {obj.unit_name}"
    total_supply_display.short_description = "Total supply"


@admin.register(Tokenization)
class TokenizationAdmin(admin.ModelAdmin):
    list_display = ("real_world_object", "asset", "status", "supervisor", "default_reason", "defaulted_at", "created_at")
    list_filter = ("status", "default_reason", "asset", "created_at")
    search_fields = (
        "real_world_object__name",
        "real_world_object__slug",
        "asset__name",
        "asset__unit_name",
        "supervisor__display_name",
        "default_note",
    )
    readonly_fields = ("created_at", "defaulted_at")
    raw_id_fields = ("real_world_object", "asset", "supervisor", "defaulted_by")

    def has_delete_permission(self, request, obj=None):
        return False


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


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'symbol', 'asset', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')
    raw_id_fields = ('asset',)


class _AceYamlWidget(django_forms.Textarea):
    """Textarea replaced by an Ace editor (YAML mode) in the admin."""

    class Media:
        js = ["https://cdnjs.cloudflare.com/ajax/libs/ace/1.36.4/ace.js"]

    def render(self, name, value, attrs=None, renderer=None):
        if attrs is None:
            attrs = {}
        field_id = attrs.get("id", f"id_{name}")
        ace_div_id = f"ace_editor_{field_id}"
        attrs["style"] = "display:none"
        textarea_html = super().render(name, value, attrs, renderer)
        init_value = (value or "").replace("\\", "\\\\").replace("`", "\\`")
        script = mark_safe(f"""
<div id="{ace_div_id}" style="height:500px;border:1px solid #ccc;border-radius:4px"></div>
{textarea_html}
<script>
(function() {{
  function initAce() {{
    if (typeof ace === 'undefined') {{ setTimeout(initAce, 50); return; }}
    var editor = ace.edit("{ace_div_id}");
    editor.setTheme("ace/theme/monokai");
    editor.session.setMode("ace/mode/yaml");
    editor.setFontSize(13);
    editor.setValue(`{init_value}`, -1);
    var ta = document.getElementById("{field_id}");
    editor.session.on('change', function() {{ ta.value = editor.getValue(); }});
  }}
  if (document.readyState === 'loading') {{
    document.addEventListener('DOMContentLoaded', initAce);
  }} else {{
    initAce();
  }}
}})();
</script>
""")
        return script


class _ContractAdminForm(django_forms.ModelForm):
    class Meta:
        from .models import Contract as _Contract
        model = _Contract
        fields = "__all__"
        widgets = {"code": _AceYamlWidget(attrs={"rows": 30})}


@admin.register(Contract)
class ContractAdmin(admin.ModelAdmin):
    form = _ContractAdminForm
    list_display = ("name", "has_code", "created_at")
    search_fields = ("name", "code")
    readonly_fields = ("created_at",)

    @staticmethod
    def has_code(obj):
        return bool(obj.code)
    has_code.boolean = True
    has_code.short_description = "Has code"


@admin.register(Agreement)
class AgreementAdmin(admin.ModelAdmin):
    list_display = ("uuid", "source_account", "target_account", "contract", "created_at")
    search_fields = (
        "uuid",
        "source_account__code",
        "source_account__name",
        "target_account__code",
        "target_account__name",
        "contract__name",
    )
    readonly_fields = ("uuid", "created_at")
    raw_id_fields = ("source_account", "target_account", "contract")


@admin.register(Obligation)
class ObligationAdmin(admin.ModelAdmin):
    list_display = ('reference', 'debtor_account', 'creditor_account', 'asset', 'amount_display', 'due_at', 'status')
    list_filter = ('status', 'asset')
    search_fields = ('reference', 'order_reference', 'debtor_account__code', 'creditor_account__code')
    readonly_fields = ('created_at', 'updated_at', 'fulfilled_at', 'amount_display', 'collateral_display')
    raw_id_fields = ('debtor_account', 'creditor_account', 'asset', 'collateral_account', 'collateral_asset')

    @staticmethod
    def amount_display(obj):
        return f"{obj.amount_display} {obj.asset.unit_name}"
    amount_display.short_description = "Amount"

    @staticmethod
    def collateral_display(obj):
        if obj.collateral_asset:
            return f"{obj.collateral_display} {obj.collateral_asset.unit_name}"
        return "—"
    collateral_display.short_description = "Collateral"




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
