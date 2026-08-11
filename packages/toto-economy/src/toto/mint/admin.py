from django.contrib import admin

from .models import CurrencyMintEvent, IssuanceRecord


@admin.register(IssuanceRecord)
class IssuanceRecordAdmin(admin.ModelAdmin):
    list_display = ("unit_name", "max_supply_base_units", "actor",
                    "created_at")
    search_fields = ("unit_name", "currency_hash", "reason")
    raw_id_fields = ("asset", "actor", "ledger_transaction")

    # Issuance is irreversible and this is its only account of itself.
    readonly_fields = [f.name for f in IssuanceRecord._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CurrencyMintEvent)
class CurrencyMintEventAdmin(admin.ModelAdmin):
    list_display = ("sequence", "kind", "currency_hash", "amount_base_units",
                    "actor", "created_at")
    list_filter = ("kind",)
    search_fields = ("currency_hash", "event_hash", "reason")
    raw_id_fields = ("asset", "actor", "ledger_transaction")

    # Every field, because every field is chained: editing any of them here
    # would break the hash and every link after it.
    readonly_fields = [f.name for f in CurrencyMintEvent._meta.fields]

    def has_add_permission(self, request):
        # Minting goes through the mint tab, where it gets a ledger posting
        # and a signature. A row typed in here would have neither.
        return False

    def has_delete_permission(self, request, obj=None):
        return False
