from django.contrib import admin

from .models import IssuanceRecord


@admin.register(IssuanceRecord)
class IssuanceRecordAdmin(admin.ModelAdmin):
    list_display = ("unit_name", "total_supply_base_units", "actor",
                    "created_at")
    search_fields = ("unit_name", "currency_hash", "reason")
    raw_id_fields = ("asset", "actor", "ledger_transaction")

    # Issuance is irreversible and this is its only account of itself.
    readonly_fields = [f.name for f in IssuanceRecord._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
