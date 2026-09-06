from django.contrib import admin

from toto.company.models import (
    Company,
    CompanyEvent,
    CompanyForum,
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


# The two join rows. Registered because the admin is the ONLY way to make one:
# neither has a form, a service or a page of its own, so without these a
# company's calendar and its forum link can only be created from a shell —
# which means they render empty forever on a live host.

@admin.register(CompanyEvent)
class CompanyEventAdmin(admin.ModelAdmin):
    list_display = ("company", "event", "created_at")
    list_filter = ("company",)
    search_fields = ("company__name", "event__title")
    autocomplete_fields = ("event",)


@admin.register(CompanyForum)
class CompanyForumAdmin(admin.ModelAdmin):
    """One room per company. `channel_slug` is a SLUG, not a foreign key —
    see the model docstring for why — so it is typed rather than picked, and
    nothing here checks that the room exists. A slug naming no room renders
    no link on the company page, which is the designed behaviour and not a
    validation gap to close here."""

    list_display = ("company", "channel_slug", "created_at")
    search_fields = ("company__name", "channel_slug")
