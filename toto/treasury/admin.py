from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import Budget, BudgetItem, BudgetLedgerAccount, BudgetStreamType


@admin.register(Budget)
class BudgetAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "status", "asset", "created_by", "created_at")
    list_filter = ("status",)
    search_fields = ("code", "name", "owner_type", "owner_id")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ["asset", "budget_account", "created_by"]


@admin.register(BudgetLedgerAccount)
class BudgetLedgerAccountAdmin(admin.ModelAdmin):
    list_display = ("budget", "ledger_account", "role", "is_default", "is_active", "can_receive", "can_pay")
    list_filter = ("role", "is_active", "is_default")
    search_fields = ("budget__code", "ledger_account__code")
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ["budget", "ledger_account"]


@admin.register(BudgetStreamType)
class BudgetStreamTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "direction", "namespace", "is_system", "is_active", "sort_order")
    list_filter = ("direction", "is_system", "is_active", "namespace")
    search_fields = ("code", "name", "namespace")
    readonly_fields = ("created_at", "updated_at")


@admin.register(BudgetItem)
class BudgetItemAdmin(admin.ModelAdmin):
    list_display = (
        "title", "budget", "stream_type", "status",
        "amount_base_units", "asset", "source_type", "imported_at",
    )
    list_filter = ("status", "stream_type__direction", "stream_type")
    search_fields = ("title", "description", "import_key", "source_id", "external_reference")
    readonly_fields = (
        "import_key", "imported_at", "source_type", "source_id", "source_label",
        "created_at", "updated_at",
    )
    raw_id_fields = [
        "ledger_transaction", "obligation", "allocation",
        "contract", "counterparty", "account_binding", "paired_item",
    ]
    autocomplete_fields = ["budget", "stream_type", "asset", "created_by"]
    fieldsets = (
        (None, {"fields": ("budget", "stream_type", "account_binding", "status", "title", "description")}),
        (_("Financial"), {"fields": ("asset", "amount_base_units")}),
        (_("Object Links"), {"fields": (
            "ledger_transaction", "obligation", "allocation",
            "contract", "counterparty", "paired_item",
        ), "classes": ("collapse",)}),
        (_("Source"), {"fields": (
            "source_type", "source_id", "source_label", "source_url",
            "import_key", "external_reference", "imported_at",
        ), "classes": ("collapse",)}),
        (_("Timing"), {"fields": ("due_at", "booked_at"), "classes": ("collapse",)}),
        (_("Metadata"), {"fields": ("metadata", "created_by", "created_at", "updated_at"), "classes": ("collapse",)}),
    )
