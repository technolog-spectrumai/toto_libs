from django.contrib import admin

from .models import PractitionerAllowance, PractitionerIncomeProfile, ProjectTokenization


@admin.register(ProjectTokenization)
class ProjectTokenizationAdmin(admin.ModelAdmin):
    list_display = ("project", "asset", "status", "supervisor", "created_at")
    list_filter = ("status",)
    search_fields = ("project__name", "asset__unit_name")
    raw_id_fields = ("project", "asset", "supervisor", "defaulted_by")
    readonly_fields = ("created_at",)


@admin.register(PractitionerIncomeProfile)
class PractitionerIncomeProfileAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "default_income_account")
    search_fields = ("practitioner__person__display_name",)
    raw_id_fields = ("practitioner", "default_income_account")


class PractitionerAllowanceInline(admin.TabularInline):
    model = PractitionerAllowance
    extra = 0
    fields = ("allowance_type", "asset", "amount_base_units", "payer_account", "recipient_account", "active")
    raw_id_fields = ("asset", "payer_account", "recipient_account")


@admin.register(PractitionerAllowance)
class PractitionerAllowanceAdmin(admin.ModelAdmin):
    list_display = ("practitioner", "allowance_type", "asset", "amount_base_units", "active", "valid_from", "valid_until")
    list_filter = ("allowance_type", "active")
    raw_id_fields = ("practitioner", "asset", "payer_account", "recipient_account")
