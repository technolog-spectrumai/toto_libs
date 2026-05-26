from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import BillingMetric, BillingUnit, Tariff, TariffItem, UsageCharge, UsageRecord


@admin.register(BillingUnit)
class BillingUnitAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "dimension", "app_label", "active")
    list_filter = ("active", "dimension", "app_label")
    search_fields = ("code", "label", "dimension", "app_label")
    ordering = ("dimension", "code")


@admin.register(BillingMetric)
class BillingMetricAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "dimension", "app_label", "default_unit", "active")
    list_filter = ("active", "dimension", "app_label")
    search_fields = ("code", "label", "description")
    ordering = ("dimension", "code")
    autocomplete_fields = ["default_unit"]


class TariffItemInline(admin.TabularInline):
    model = TariffItem
    extra = 0
    fields = (
        "metric", "name", "charged_asset", "price_per_unit_display",
        "unit", "unit_quantity", "receiving_account", "rounding_mode", "active",
    )
    readonly_fields = ("price_per_unit_base_units",)
    autocomplete_fields = ["metric", "charged_asset", "receiving_account", "unit"]


@admin.register(Tariff)
class TariffAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "status", "created_at", "updated_at")
    list_filter = ("status",)
    search_fields = ("name", "code", "source_type", "source_id")
    readonly_fields = ("uuid", "created_at", "updated_at")
    inlines = [TariffItemInline]
    fieldsets = (
        (None, {"fields": ("uuid", "name", "code", "status", "description")}),
        (_("Source"), {"fields": ("source_type", "source_id"), "classes": ("collapse",)}),
        (_("Metadata"), {"fields": ("metadata",), "classes": ("collapse",)}),
        (_("Timestamps"), {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(TariffItem)
class TariffItemAdmin(admin.ModelAdmin):
    list_display = (
        "metric_code_display", "tariff", "name", "charged_asset",
        "price_per_unit_display", "unit", "unit_quantity",
        "receiving_account", "rounding_mode", "active",
    )
    list_filter = ("active", "unit", "rounding_mode", "tariff")
    search_fields = ("metric__code", "name", "tariff__code", "tariff__name")
    readonly_fields = ("price_per_unit_base_units", "created_at", "updated_at")
    autocomplete_fields = ["tariff", "metric", "charged_asset", "receiving_account", "unit"]

    @admin.display(description="Metric code", ordering="metric__code")
    def metric_code_display(self, obj):
        return obj.metric.code


class UsageChargeInline(admin.TabularInline):
    model = UsageCharge
    extra = 0
    readonly_fields = (
        "tariff_item", "charged_asset", "quantity", "unit",
        "price_per_unit_base_units", "amount_base_units",
        "payer_account", "receiving_account",
    )
    can_delete = False


@admin.register(UsageRecord)
class UsageRecordAdmin(admin.ModelAdmin):
    list_display = (
        "uuid", "tariff", "payer_account", "metric_code",
        "quantity", "unit", "status", "occurred_at", "rated_at",
    )
    list_filter = ("status", "unit", "tariff")
    search_fields = ("uuid", "metric_code", "source_type", "source_id")
    readonly_fields = (
        "uuid", "tariff", "payer_account", "metric_code", "quantity", "unit",
        "source_type", "source_id", "occurred_at", "rated_at", "ledger_transaction",
        "created_at", "updated_at",
    )
    inlines = [UsageChargeInline]

    def has_add_permission(self, request):
        return False


@admin.register(UsageCharge)
class UsageChargeAdmin(admin.ModelAdmin):
    list_display = (
        "usage_record", "tariff_item", "charged_asset",
        "quantity", "unit", "amount_base_units",
        "payer_account", "receiving_account",
    )
    list_filter = ("charged_asset", "unit")
    search_fields = ("usage_record__uuid", "tariff_item__metric__code")
    readonly_fields = (
        "usage_record", "tariff_item", "charged_asset",
        "quantity", "unit", "price_per_unit_base_units", "amount_base_units",
        "payer_account", "receiving_account", "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
