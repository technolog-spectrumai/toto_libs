from django.contrib import admin

from .models import (
    AssemblyDecision,
    AssemblyProposal,
    AssemblyVote,
    CommunityAssemblyConfig,
    CommunitySenate,
    CommunityRule,
    CommunityTransactionFee,
    PollTax,
    PollTaxPayment,
    SenateVeto,
)


class AssemblyVoteInline(admin.TabularInline):
    model = AssemblyVote
    extra = 0
    readonly_fields = ("voted_at",)


@admin.register(CommunityAssemblyConfig)
class CommunityAssemblyConfigAdmin(admin.ModelAdmin):
    list_display = ("community", "quorum_fraction")
    search_fields = ("community__name",)


@admin.register(AssemblyProposal)
class AssemblyProposalAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "proposal_type", "is_repeal", "status", "opened_by", "closes_at", "created_at")
    list_filter = ("proposal_type", "is_repeal", "status", "community", "created_at")
    search_fields = ("title", "body", "community__name", "opened_by__display_name")
    inlines = [AssemblyVoteInline]


@admin.register(AssemblyVote)
class AssemblyVoteAdmin(admin.ModelAdmin):
    list_display = ("proposal", "voter", "vote", "voted_at")
    list_filter = ("vote", "voted_at")
    search_fields = ("proposal__title", "voter__display_name")


@admin.register(AssemblyDecision)
class AssemblyDecisionAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "decision_type", "content_hash", "created_at")
    list_filter = ("decision_type", "community", "created_at")
    readonly_fields = ("content_hash", "prev_hash", "created_at", "content", "community", "proposal", "decision_type", "title")
    search_fields = ("title", "community__name", "content_hash")

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(CommunityRule)
class CommunityRuleAdmin(admin.ModelAdmin):
    list_display = ("title", "community", "active", "decision", "created_at")
    list_filter = ("active", "community")
    search_fields = ("title", "text", "community__name")


@admin.register(CommunityTransactionFee)
class CommunityTransactionFeeAdmin(admin.ModelAdmin):
    list_display = ("community", "asset", "fee_bps", "set_by", "active", "decision", "created_at")
    list_filter = ("active", "set_by", "community", "asset")
    search_fields = ("community__name",)


@admin.register(PollTax)
class PollTaxAdmin(admin.ModelAdmin):
    list_display = ("community", "payment_asset", "head_amount_base_units", "wealth_rate_bps", "wealth_asset", "period", "active")
    list_filter = ("active", "period", "community")
    search_fields = ("community__name",)


@admin.register(PollTaxPayment)
class PollTaxPaymentAdmin(admin.ModelAdmin):
    list_display = ("person", "poll_tax", "period_label", "total_paid_base_units", "paid_at")
    list_filter = ("period_label", "paid_at")
    search_fields = ("person__display_name", "poll_tax__community__name")
    readonly_fields = ("paid_at",)


@admin.register(CommunitySenate)
class CommunitySenateAdmin(admin.ModelAdmin):
    list_display = ("community", "is_active", "veto_window_days", "created_at")
    list_editable = ("is_active", "veto_window_days")
    raw_id_fields = ("community",)
    filter_horizontal = ("members",)
    search_fields = ("community__name",)


@admin.register(SenateVeto)
class SenateVetoAdmin(admin.ModelAdmin):
    list_display = ("proposal", "senator", "vetoed_at")
    raw_id_fields = ("proposal", "senator")
    readonly_fields = ("vetoed_at",)
    search_fields = ("proposal__title", "senator__display_name")
