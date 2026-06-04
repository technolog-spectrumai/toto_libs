from django.contrib import admin

from .models import (
    Allocation,
    AllocationKind,
    AllocationStatus,
    Condition,
    ConditionKind,
    ConditionStatus,
    ContractEvent,
    ContractEventKind,
    Entitlement,
    EntitlementKind,
    EntitlementStatus,
    Schedule,
    ScheduleKind,
    ScheduleStatus,
)


@admin.register(Entitlement)
class EntitlementAdmin(admin.ModelAdmin):
    list_display = ("resource_label", "kind", "status", "holder_account", "source_type", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("resource_label", "source_type", "source_id")
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "status", "frequency", "next_run_at", "source_type", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("name", "source_type", "source_id")
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(Condition)
class ConditionAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "status", "source_type", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("name", "source_type", "source_id")
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(Allocation)
class AllocationAdmin(admin.ModelAdmin):
    list_display = ("kind", "status", "asset", "amount_base_units", "source_type", "created_at")
    list_filter = ("kind", "status")
    search_fields = ("source_type", "source_id")
    readonly_fields = ("uuid", "created_at", "updated_at")


@admin.register(ContractEvent)
class ContractEventAdmin(admin.ModelAdmin):
    list_display = ("kind", "title", "agreement", "source_type", "created_at")
    list_filter = ("kind",)
    search_fields = ("title", "source_type", "source_id")
    readonly_fields = ("uuid", "created_at")
