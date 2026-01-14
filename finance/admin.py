from django.contrib import admin, messages
from toto.batch import BatchAction
from .models import Currency, Account, Transaction


# 💱 Currency Admin
@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "is_crypto", "decimals", "active")
    list_filter = ("is_crypto", "active")
    search_fields = ("name", "symbol")
    ordering = ("symbol",)


# 🏦 Account Admin
@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "owner",
        "currency",
        "balance",
        "active",
        "created_at",
    )
    list_filter = ("currency", "active")
    search_fields = ("name", "owner__legal_name", "owner__registration_number")
    autocomplete_fields = ("owner", "manager", "currency")
    readonly_fields = ("created_at",)
    ordering = ("name",)


# 💸 Transaction Admin
@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "amount",
        "source",
        "destination",
        "conversion_rate",
        "timestamp",
    )
    list_filter = ("source__currency", "destination__currency")
    search_fields = (
        "name",
        "source__name",
        "destination__name",
        "source__owner__legal_name",
        "destination__owner__legal_name",
    )
    autocomplete_fields = ("source", "destination")
    readonly_fields = ("timestamp",)

    actions = ["execute_transactions"]

    # Optional: prevent editing after creation
    def has_change_permission(self, request, obj=None):
        if obj:
            return False  # transactions are immutable
        return True

    # ---------------------------------------------------------
    #  ADMIN ACTION: EXECUTE TRANSACTIONS
    # ---------------------------------------------------------
    @admin.action(description="Execute selected transactions")
    def execute_transactions(self, request, queryset):

        def execute_one(tx: Transaction):
            try:
                tx.execute()
                return tx
            except Exception as e:
                raise ValueError(f"Transaction '{tx.name}' failed: {e}")

        result = BatchAction(queryset).run(execute_one)

        BatchAction.display_messages(
            result,
            self.message_user,
            request,
            verb="executed"
        )
