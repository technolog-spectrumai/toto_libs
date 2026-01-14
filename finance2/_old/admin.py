from django.contrib import admin
from toto.finance.models import Currency, Account, Transaction, ExchangeRate, Asset, AssetType
from toto.batch import BatchAction
from toto.admin import BaseSerializableAdmin
from django import forms
from django_json_widget.widgets import JSONEditorWidget


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "is_crypto", "decimals", "active")
    search_fields = ("name", "symbol")
    list_filter = ("is_crypto", "active")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("base_currency", "quote_currency", "rate", "timestamp")
    list_filter = ("base_currency", "quote_currency")
    search_fields = ("base_currency__symbol", "quote_currency__symbol")
    ordering = ("-timestamp",)


@admin.register(Account)
class AccountAdmin(BaseSerializableAdmin):
    list_display = ("name", "owner_display", "manager", "currency", "balance", "active", "created_at")
    search_fields = ("name", "owner__name")
    list_filter = ("currency", "active")
    readonly_fields = ("created_at",)

    def owner_display(self, obj):
        """
        Show the polymorphic identity of the owner (CommunityMember, Company, etc.)
        """
        if obj.owner:
            real_instance = obj.owner.get_real_instance_class()
            return f"{real_instance.__name__}: id={obj.owner.id}"
        return "-"
    owner_display.short_description = "Owner"


@admin.register(Transaction)
class TransactionAdmin(BaseSerializableAdmin):
    list_display = ("name", "amount", "currency", "source", "destination", "timestamp")
    search_fields = ("name", "source__name", "destination__name")
    list_filter = ("currency", "timestamp")
    readonly_fields = ("timestamp",)
    actions = ["execute_transactions"]

    @admin.action(description="Execute selected transactions")
    def execute_transactions(self, request, queryset):
        def execute_one(tx):
            tx.execute()
            return tx

        result = BatchAction(queryset).run(execute_one)
        BatchAction.display_messages(result, self.message_user, request, verb="execute")



# 📝 Asset Admin Form with JSON editor
class AssetAdminForm(forms.ModelForm):
    description = forms.CharField(widget=forms.Textarea, required=False)
    metadata = forms.JSONField(widget=JSONEditorWidget, required=False)

    class Meta:
        model = Asset
        fields = "__all__"


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    form = AssetAdminForm
    list_display = (
        "name",
        "asset_type",
        "serial_number",
        "assigned_to",
        "location",
        "is_active",
        "purchase_date",
        "purchase_price",
        "created_at",
    )
    list_filter = ("asset_type", "is_active", "purchase_date")
    search_fields = ("name", "serial_number", "assigned_to__username", "location__city")
    autocomplete_fields = ("asset_type", "assigned_to", "location")
    readonly_fields = ("created_at",)
    ordering = ("name",)

    fieldsets = (
        (None, {
            "fields": ("name", "asset_type", "description", "metadata")
        }),
        ("Identification", {
            "fields": ("serial_number", "location")
        }),
        ("Ownership", {
            "fields": ("assigned_to", "is_active")
        }),
        ("Purchase Info", {
            "fields": ("purchase_date", "purchase_price")
        }),
        ("Timestamps", {
            "fields": ("created_at",)
        }),
    )


