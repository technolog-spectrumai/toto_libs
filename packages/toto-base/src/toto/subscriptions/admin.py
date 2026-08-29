from django.contrib import admin

from .models import (
    CommunityDiscount,
    PlanAudience,
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


@admin.register(CommunityDiscount)
class CommunityDiscountAdmin(admin.ModelAdmin):
    # One number per community, every plan. The Discounts tab is the everyday
    # door; this is the escape hatch.
    list_display = ("community", "percent")
    list_editable = ("percent",)
    autocomplete_fields = ("community",)
    search_fields = ("community__name",)


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


@admin.register(PlanAudience)
class PlanAudienceAdmin(admin.ModelAdmin):
    # No rows for a plan = offered to everybody. The Communities tab is the
    # everyday door; this is the escape hatch, and the same rule applies —
    # deleting the last row here makes the plan public again.
    list_display = ("plan", "community", "created_at")
    list_filter = ("plan",)
    autocomplete_fields = ("plan", "community")
    search_fields = ("plan__name", "plan__code", "community__name")
