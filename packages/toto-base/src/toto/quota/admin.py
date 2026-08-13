"""Reusable admin bases.

This app registers nothing — it has no models. Each app registers its own
concrete pair and can inherit these to get consistent columns and filters::

    from toto.quota.admin import QuotaPolicyAdminBase, UsageEventAdminBase

    @admin.register(VaultQuotaPolicy)
    class VaultQuotaPolicyAdmin(QuotaPolicyAdminBase):
        pass
"""

from django.contrib import admin


class QuotaPolicyAdminBase(admin.ModelAdmin):
    """One row per metric, applying to everyone.

    There is no user column because there is no per-person policy: somebody who
    needs more room holds an office, and ``Station.limit_multiplier`` scales
    every limit they are subject to.
    """

    list_display = ("metric_code", "limit", "unit", "period", "mode", "active")
    list_filter = ("period", "mode", "active", "metric_code")
    search_fields = ("name", "metric_code")
    list_editable = ("active", "mode")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "metric_code", "active")}),
        ("Limit", {"fields": ("limit", "unit", "period", "mode")}),
        ("Time bounds", {"fields": ("starts_at", "ends_at"), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


class UsageEventAdminBase(admin.ModelAdmin):
    list_display = ("metric_code", "quantity", "unit", "user", "status", "occurred_at")
    list_filter = ("metric_code", "status")
    search_fields = ("metric_code", "user__username", "source_id", "idempotency_key")
    readonly_fields = ("created_at",)
    date_hierarchy = "occurred_at"

    def has_add_permission(self, request):
        # Usage is recorded by the code that consumed the resource, never typed in.
        return False
