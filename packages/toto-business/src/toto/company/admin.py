from django.contrib import admin

from toto.company.models import (
    Company,
    CompanyMembership,
    Department,
    DepartmentMembership,
    OwnershipEvent,
    Party,
    ShareClass,
    ShareHolding,
)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "form", "registry_no", "active")
    list_filter = ("form", "active")
    search_fields = ("name", "registry_no", "tax_no", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Party)
class PartyAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "is_organisation", "active")
    list_filter = ("company", "is_organisation", "active")
    search_fields = ("name", "registry_no", "tax_no")
    autocomplete_fields = ("company",)


@admin.register(ShareClass)
class ShareClassAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "votes_per_unit", "active")
    list_filter = ("company", "active")
    search_fields = ("name", "slug")


@admin.register(ShareHolding)
class ShareHoldingAdmin(admin.ModelAdmin):
    list_display = ("party", "share_class", "units", "since", "until")
    list_filter = ("share_class__company",)
    search_fields = ("party__name",)


@admin.register(OwnershipEvent)
class OwnershipEventAdmin(admin.ModelAdmin):
    """Read-only on purpose: these rows are append-only, and the model's own
    save()/delete() raise. Letting admin offer buttons that always fail would
    be worse than not offering them."""

    list_display = ("company", "event_type", "effective_on", "source_party", "target_party")
    list_filter = ("company", "event_type")
    date_hierarchy = "effective_on"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "company", "parent", "head", "active")
    list_filter = ("company", "active")
    search_fields = ("name", "slug")


@admin.register(DepartmentMembership)
class DepartmentMembershipAdmin(admin.ModelAdmin):
    list_display = ("party", "department", "title", "is_leadership", "active")
    list_filter = ("department__company", "is_leadership", "active")
    search_fields = ("party__name", "title")


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(admin.ModelAdmin):
    list_display = ("person", "company", "job_title", "primary_department", "active")
    list_filter = ("company", "active")
    search_fields = ("person__display_name", "job_title")
