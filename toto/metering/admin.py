from django.contrib import admin

from .models import EventStatus, UsageEvent, UsageMetric, UsageQuota


@admin.register(UsageMetric)
class UsageMetricAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "namespace", "default_unit", "is_active", "created_at"]
    list_filter = ["is_active", "namespace"]
    search_fields = ["code", "name", "namespace"]
    readonly_fields = ["created_at", "updated_at"]
    prepopulated_fields = {"code": ("name",)}


@admin.register(UsageEvent)
class UsageEventAdmin(admin.ModelAdmin):
    list_display = [
        "uid", "metric", "quantity", "unit", "status",
        "subject_type", "subject_id", "source_type", "occurred_at",
    ]
    list_filter = ["status", "metric", "source_type", "subject_type"]
    search_fields = ["uid", "idempotency_key", "source_id", "subject_id", "description"]
    readonly_fields = ["uid", "created_at", "updated_at"]
    date_hierarchy = "occurred_at"

    actions = ["void_selected"]

    @admin.action(description="Void selected events")
    def void_selected(self, request, queryset):
        n = queryset.exclude(status=EventStatus.VOIDED).update(status=EventStatus.VOIDED)
        self.message_user(request, f"{n} event(s) voided.")


@admin.register(UsageQuota)
class UsageQuotaAdmin(admin.ModelAdmin):
    list_display = [
        "code", "name", "metric", "limit_quantity", "unit",
        "period", "mode", "subject_type", "subject_id", "is_active",
    ]
    list_filter = ["is_active", "period", "mode", "metric"]
    search_fields = ["code", "name", "subject_id", "subject_type"]
    readonly_fields = ["created_at", "updated_at"]
