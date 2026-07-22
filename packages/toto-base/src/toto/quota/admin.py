from django.contrib import admin

from .models import QuotaPolicy, UsageEvent


@admin.register(QuotaPolicy)
class QuotaPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "app_label", "metric_code", "limit", "unit", "period", "mode", "active", "subject_type", "subject_id")
    list_filter = ("app_label", "period", "mode", "active")
    search_fields = ("name", "app_label", "metric_code", "subject_id")
    list_editable = ("active", "mode")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "app_label", "metric_code", "active")}),
        ("Subject", {"fields": ("subject_type", "subject_id"), "classes": ("collapse",)}),
        ("Limit", {"fields": ("limit", "unit", "period", "mode")}),
        ("Time bounds", {"fields": ("starts_at", "ends_at"), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = ("app_label", "metric_code", "quantity", "unit", "subject_type", "subject_id", "status", "occurred_at")
    list_filter = ("app_label", "metric_code", "status")
    search_fields = ("app_label", "metric_code", "subject_id", "source_id", "idempotency_key")
    readonly_fields = ("created_at",)
    date_hierarchy = "occurred_at"
