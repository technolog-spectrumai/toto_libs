from django.contrib import admin

from .models import (
    AcceptedObservation, Dataset, DatasetVersion, DatasetVersionMember,
    HesperisBounty, HesperisCampaign,
)


@admin.register(HesperisCampaign)
class HesperisCampaignAdmin(admin.ModelAdmin):
    list_display = ("campaign", "is_open", "licence", "gem_asset", "treasury_account")
    list_filter = ("is_open",)
    raw_id_fields = ("campaign", "gem_asset", "treasury_account")


@admin.register(HesperisBounty)
class HesperisBountyAdmin(admin.ModelAdmin):
    list_display = ("mission", "opens_at", "closes_at", "max_contributions")
    search_fields = ("mission__title", "instructions")
    raw_id_fields = ("mission",)


@admin.register(AcceptedObservation)
class AcceptedObservationAdmin(admin.ModelAdmin):
    """Read-only. An observation is created by services.accept and nothing else.

    Offering an add form here would be a second way for a fact to enter a
    dataset without having survived review — which is the one thing the
    raw/accepted split exists to make impossible.
    """

    list_display = ("pk", "bounty", "observed_by", "observed_at", "accepted_at")
    search_fields = ("bounty__mission__title", "observed_by__display_name")
    raw_id_fields = ("submission", "bounty", "observed_by", "location", "zone")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("name", "campaign", "bucket", "licence", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    # raw_id for the bucket too: a host can hold thousands, and a select box
    # that loads all of them is how this page stops rendering.
    raw_id_fields = ("campaign", "bucket")


class DatasetVersionMemberInline(admin.TabularInline):
    model = DatasetVersionMember
    extra = 0
    raw_id_fields = ("observation",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(DatasetVersion)
class DatasetVersionAdmin(admin.ModelAdmin):
    """A frozen release. Membership and the hash are never edited here."""

    list_display = ("dataset", "number", "frozen_at", "frozen_by", "verified")
    readonly_fields = ("manifest_hash", "frozen_at", "number")
    raw_id_fields = ("dataset", "frozen_by")
    inlines = [DatasetVersionMemberInline]

    def has_add_permission(self, request):
        return False

    @admin.display(boolean=True, description="Unchanged since freezing")
    def verified(self, obj):
        return obj.verify()
