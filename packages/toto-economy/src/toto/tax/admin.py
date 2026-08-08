from django.contrib import admin

from .models import (
    SurplusCharge, SurplusPolicy, TaxArrearsCase, TaxEnforcementAction,
    TaxRule, TimeGrant,
)


@admin.register(SurplusPolicy)
class SurplusPolicyAdmin(admin.ModelAdmin):
    list_display = ("asset", "threshold_display", "rate", "period", "active",
                    "updated_at")
    list_filter = ("active", "period")


@admin.register(SurplusCharge)
class SurplusChargeAdmin(admin.ModelAdmin):
    list_display = ("policy", "user", "period_label", "fee_base", "status",
                    "collected_at")
    list_filter = ("status", "policy")
    search_fields = ("user__username", "period_label")
    readonly_fields = [f.name for f in SurplusCharge._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(TimeGrant)
class TimeGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "key", "scope_id", "seconds", "updated_at")
    list_filter = ("key",)
    search_fields = ("user__username", "key")


@admin.register(TaxRule)
class TaxRuleAdmin(admin.ModelAdmin):
    list_display = ("metric_code", "allowance", "unit_label", "active", "updated_at")
    list_filter = ("active",)
    search_fields = ("metric_code",)


class TaxEnforcementActionInline(admin.TabularInline):
    model = TaxEnforcementAction
    extra = 0
    can_delete = False
    readonly_fields = ("action", "item_pk", "item_label", "item_key",
                       "container", "size_raw", "created_at")


@admin.register(TaxArrearsCase)
class TaxArrearsCaseAdmin(admin.ModelAdmin):
    list_display = ("user", "rule", "status", "failed_days", "warned_at",
                    "deadline_at", "reached_target")
    list_filter = ("status", "reached_target", "rule")
    search_fields = ("user__username", "uuid")
    readonly_fields = ("uuid", "opened_at", "warned_at", "deadline_at",
                       "resolved_at", "enforced_at", "warning_event_uid",
                       "enforcement_summary", "created_at", "updated_at")
    inlines = [TaxEnforcementActionInline]
