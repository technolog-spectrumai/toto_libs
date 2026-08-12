from django.contrib import admin

from .models import TaxArrearsCase, TaxRule, TimeGrant


@admin.register(TimeGrant)
class TimeGrantAdmin(admin.ModelAdmin):
    list_display = ("user", "key", "scope_id", "seconds", "updated_at")
    list_filter = ("key",)
    search_fields = ("user__username", "key")


@admin.register(TaxRule)
class TaxRuleAdmin(admin.ModelAdmin):
    list_display = ("metric_code", "unit_label", "active", "concentration_k",
                    "updated_at")
    list_editable = ("active", "concentration_k")
    list_filter = ("active",)
    search_fields = ("metric_code",)


@admin.register(TaxArrearsCase)
class TaxArrearsCaseAdmin(admin.ModelAdmin):
    """Read-only: a case is written by the nightly run, never by hand.

    Past its deadline a case makes ``arrears.is_frozen()`` answer True and new
    metered writes refuse — that is the whole consequence. Nothing is deleted,
    so there is no enforcement record to show and nothing here to undo: paying,
    or shedding what is held, resolves the case on the next sweep.
    """

    list_display = ("user", "rule", "status", "failed_days", "warned_at",
                    "deadline_at")
    list_filter = ("status", "rule")
    search_fields = ("user__username", "uuid")
    readonly_fields = ("uuid", "opened_at", "warned_at", "deadline_at",
                       "resolved_at", "warning_event_uid", "created_at",
                       "updated_at")
