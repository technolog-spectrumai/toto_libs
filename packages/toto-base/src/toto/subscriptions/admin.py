from django.contrib import admin

from .models import (
    CommunityPlanDiscount,
    Subscription,
    SubscriptionCharge,
    SubscriptionPlan,
    SubscriptionQuotaPolicy,
)


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "units", "is_default", "active", "order")
    list_editable = ("units", "active", "order")
    list_filter = ("active", "is_default")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}


@admin.register(CommunityPlanDiscount)
class CommunityPlanDiscountAdmin(admin.ModelAdmin):
    list_display = ("community", "plan", "percent")
    list_editable = ("percent",)
    list_filter = ("plan",)
    autocomplete_fields = ("community", "plan")
    search_fields = ("community__name", "plan__name")


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan", "state", "anchor_date", "arrears_since")
    list_filter = ("state", "plan")
    search_fields = ("user__username", "user__email")
    autocomplete_fields = ("user", "plan")
    # Frozen after creation: the anchor defines every period label, which is
    # half the idempotency key of every charge this subscription will ever have.
    readonly_fields = ("anchor_date", "started_at", "changed_at")


@admin.register(SubscriptionCharge)
class SubscriptionChargeAdmin(admin.ModelAdmin):
    list_display = ("subscription", "period_label", "units", "discount_percent",
                    "status", "settled_at")
    list_filter = ("status",)
    search_fields = ("subscription__user__username", "period_label")
    readonly_fields = ("subscription", "period_label", "plan_name", "units",
                       "discount_percent", "discount_source", "settled_at",
                       "created_at")

    def has_add_permission(self, request):
        # Charges are materialized from a subscription, never hand-made: a row
        # with a period label nothing produced would break the idempotency the
        # unique constraint exists to give.
        return False


@admin.register(SubscriptionQuotaPolicy)
class SubscriptionQuotaPolicyAdmin(admin.ModelAdmin):
    list_display = ("metric_code", "limit", "period", "mode", "active")
