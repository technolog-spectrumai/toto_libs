from django.contrib import admin

from toto.tribunal.models import (
    JurySession,
    JuryVote,
    TribunalCase,
    TribunalParty,
    TribunalClaim,
    TribunalEvidence,
    TribunalRuling,
)


class TribunalPartyInline(admin.TabularInline):
    model = TribunalParty
    extra = 0


class TribunalClaimInline(admin.TabularInline):
    model = TribunalClaim
    extra = 0


class TribunalEvidenceInline(admin.TabularInline):
    model = TribunalEvidence
    extra = 0


class TribunalRulingInline(admin.StackedInline):
    model = TribunalRuling
    extra = 0


@admin.register(TribunalCase)
class TribunalCaseAdmin(admin.ModelAdmin):
    list_display = (
        "case_number",
        "title",
        "status",
        "priority",
        "opened_by",
        "assigned_to",
        "related_object",
        "related_order",
        "opened_at",
        "resolved_at",
    )
    list_filter = ("status", "priority", "opened_at", "resolved_at")
    search_fields = (
        "case_number",
        "title",
        "reason",
        "description",
        "opened_by__display_name",
        "assigned_to__display_name",
        "related_object__name",
        "related_order__order_number",
    )
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("case_number", "opened_at")
    inlines = [
        TribunalPartyInline,
        TribunalClaimInline,
        TribunalEvidenceInline,
        TribunalRulingInline,
    ]


@admin.register(TribunalParty)
class TribunalPartyAdmin(admin.ModelAdmin):
    list_display = ("case", "person", "role", "created_at")
    list_filter = ("role", "created_at")
    search_fields = ("case__title", "case__case_number", "person__display_name", "role", "statement")


@admin.register(TribunalClaim)
class TribunalClaimAdmin(admin.ModelAdmin):
    list_display = ("case", "summary", "claim_type", "claimant", "amount", "currency", "created_at")
    list_filter = ("claim_type", "currency", "created_at")
    search_fields = ("case__title", "case__case_number", "summary", "description")


@admin.register(TribunalEvidence)
class TribunalEvidenceAdmin(admin.ModelAdmin):
    list_display = ("case", "title", "evidence_type", "submitted_by", "related_object", "submitted_at")
    list_filter = ("evidence_type", "submitted_at")
    search_fields = ("case__title", "case__case_number", "title", "description", "file_hash", "external_reference")


@admin.register(TribunalRuling)
class TribunalRulingAdmin(admin.ModelAdmin):
    list_display = (
        "case",
        "title",
        "resolution_type",
        "decided_by",
        "amount_awarded",
        "currency",
        "fine_amount",
        "fine_asset",
        "fine_obligation",
        "effective_at",
        "created_at",
    )
    list_filter = ("resolution_type", "currency", "fine_asset", "effective_at", "created_at")
    search_fields = ("case__title", "case__case_number", "title", "decision", "reasoning")


class JuryVoteInline(admin.TabularInline):
    model = JuryVote
    extra = 0
    readonly_fields = ('voted_at',)


@admin.register(JurySession)
class JurySessionAdmin(admin.ModelAdmin):
    list_display = ("pk", "case", "status", "outcome", "opens_at", "closes_at", "created_by", "created_at")
    list_filter = ("status", "outcome", "created_at")
    search_fields = ("case__case_number", "case__title", "created_by__display_name")
    inlines = [JuryVoteInline]


@admin.register(JuryVote)
class JuryVoteAdmin(admin.ModelAdmin):
    list_display = ("session", "juror", "vote", "voted_at")
    list_filter = ("vote", "voted_at")
    search_fields = ("session__case__case_number", "juror__display_name", "reason")

