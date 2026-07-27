from django.contrib import admin

from .models import IngestProposal


@admin.register(IngestProposal)
class IngestProposalAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "total_changes", "created_by", "created_at", "applied_at")
    list_filter = ("status", "created_at")
    search_fields = ("source_text",)
    readonly_fields = ("summary", "proposal", "apply_result", "created_at", "updated_at", "applied_at")
    date_hierarchy = "created_at"
