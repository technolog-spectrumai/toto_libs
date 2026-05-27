from django.contrib import admin
from django.utils.translation import gettext_lazy as _

from .models import (
    Subscription,
    SubscriptionAllowance,
    SubscriptionCustomer,
    SubscriptionEntitlement,
    SubscriptionEvent,
    SubscriptionFeature,
    SubscriptionInvoice,
    SubscriptionInvoiceLine,
    SubscriptionPayment,
    SubscriptionPlan,
    SubscriptionPrice,
    SubscriptionUsage,
)


class SubscriptionPriceInline(admin.TabularInline):
    model = SubscriptionPrice
    extra = 0
    fields = ("name", "code", "asset", "amount_base_units", "interval", "interval_count", "receiving_account", "is_active")
    autocomplete_fields = ["asset", "receiving_account"]


class SubscriptionFeatureInline(admin.TabularInline):
    model = SubscriptionFeature
    extra = 0
    fields = ("code", "name", "kind", "quantity", "unit", "reset_interval", "hard_limit", "active")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "community", "kind", "status", "is_public", "trial_days")
    list_filter = ("status", "kind", "is_public", "community")
    search_fields = ("name", "code", "description")
    readonly_fields = ("uid", "created_at", "updated_at")
    autocomplete_fields = ["community", "owner"]
    inlines = [SubscriptionPriceInline, SubscriptionFeatureInline]


@admin.register(SubscriptionPrice)
class SubscriptionPriceAdmin(admin.ModelAdmin):
    list_display = ("plan", "code", "asset", "amount_base_units", "interval", "interval_count", "is_active")
    list_filter = ("interval", "is_active", "asset")
    search_fields = ("plan__name", "plan__code", "code", "name")
    autocomplete_fields = ["plan", "asset", "receiving_account"]


@admin.register(SubscriptionFeature)
class SubscriptionFeatureAdmin(admin.ModelAdmin):
    list_display = ("plan", "code", "name", "kind", "quantity", "unit", "hard_limit", "active")
    list_filter = ("kind", "active", "hard_limit")
    search_fields = ("plan__name", "plan__code", "code", "name")
    autocomplete_fields = ["plan"]


@admin.register(SubscriptionCustomer)
class SubscriptionCustomerAdmin(admin.ModelAdmin):
    list_display = ("person", "community", "billing_account", "default_asset", "status")
    list_filter = ("status", "community", "default_asset")
    search_fields = ("person__name", "label", "billing_account__code")
    autocomplete_fields = ["community", "person", "billing_account", "default_asset"]


class EntitlementInline(admin.TabularInline):
    model = SubscriptionEntitlement
    extra = 0
    readonly_fields = ("feature", "status", "starts_at", "ends_at")
    can_delete = False


class AllowanceInline(admin.TabularInline):
    model = SubscriptionAllowance
    extra = 0
    readonly_fields = ("feature", "period_start", "period_end", "included_quantity", "consumed_quantity")
    can_delete = False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("customer", "plan", "price", "status", "current_period_start", "current_period_end", "cancel_at_period_end")
    list_filter = ("status", "plan", "cancel_at_period_end")
    search_fields = ("customer__person__name", "plan__name", "plan__code")
    readonly_fields = ("uid", "created_at", "updated_at")
    autocomplete_fields = ["customer", "plan", "price"]
    inlines = [EntitlementInline, AllowanceInline]


class InvoiceLineInline(admin.TabularInline):
    model = SubscriptionInvoiceLine
    extra = 0
    readonly_fields = ("kind", "description", "quantity", "unit_amount_base_units", "amount_base_units")
    can_delete = False


@admin.register(SubscriptionInvoice)
class SubscriptionInvoiceAdmin(admin.ModelAdmin):
    list_display = ("uid", "subscription", "status", "total_base_units", "paid_base_units", "due_at")
    list_filter = ("status", "price__asset")
    search_fields = ("uid", "subscription__plan__code", "customer__person__name")
    readonly_fields = ("uid", "created_at", "updated_at", "ledger_transaction")
    autocomplete_fields = ["subscription", "customer", "price"]
    inlines = [InvoiceLineInline]


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "amount_base_units", "status", "reference", "paid_at")
    list_filter = ("status",)
    search_fields = ("reference", "invoice__uid")
    readonly_fields = ("ledger_transaction", "paid_at")
    autocomplete_fields = ["invoice", "payer_account", "receiving_account"]


@admin.register(SubscriptionAllowance)
class SubscriptionAllowanceAdmin(admin.ModelAdmin):
    list_display = ("subscription", "feature", "period_start", "period_end", "included_quantity", "consumed_quantity")
    list_filter = ("feature",)
    search_fields = ("subscription__plan__code", "feature__code")
    readonly_fields = ("period_start", "period_end", "included_quantity", "consumed_quantity")
    autocomplete_fields = ["subscription", "feature"]


@admin.register(SubscriptionUsage)
class SubscriptionUsageAdmin(admin.ModelAdmin):
    list_display = ("subscription", "feature", "quantity", "unit", "status", "occurred_at")
    list_filter = ("status", "feature")
    search_fields = ("subscription__plan__code", "feature__code", "note")
    autocomplete_fields = ["subscription", "feature", "allowance"]


@admin.register(SubscriptionEvent)
class SubscriptionEventAdmin(admin.ModelAdmin):
    list_display = ("kind", "community", "subscription", "actor", "created_at")
    list_filter = ("kind", "community")
    search_fields = ("kind", "subscription__plan__code")
    readonly_fields = ("uid", "created_at", "updated_at")
    autocomplete_fields = ["community", "actor", "subscription"]
