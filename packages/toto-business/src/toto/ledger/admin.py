from django.contrib import admin

from toto.ledger.models import Ledger, LedgerEntry


@admin.register(Ledger)
class LedgerAdmin(admin.ModelAdmin):
    list_display = ("name", "key", "kind", "scope_type", "algorithm", "length")
    list_filter = ("kind", "algorithm", "active")
    search_fields = ("name", "key")


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    """Read-only, because the rows are.

    Offering an edit form for a table whose triggers refuse UPDATE would be a
    button that always fails — worse than no button.
    """

    list_display = ("ledger", "sequence", "source_type", "source_ref", "recorded_at")
    list_filter = ("ledger", "source_type")
    search_fields = ("source_ref", "entry_hash", "actor_ref")
    readonly_fields = [f.name for f in LedgerEntry._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
