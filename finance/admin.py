from django.contrib import admin
from .models import Currency, Account, Transaction, ExchangeRate
from community.models import SocialEntity
from .batch import BatchAction
from toto.admin import BaseSerializableAdmin


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("name", "symbol", "is_crypto", "decimals", "active")
    search_fields = ("name", "symbol")
    list_filter = ("is_crypto", "active")


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("base_currency", "quote_currency", "rate", "timestamp")
    list_filter = ("base_currency", "quote_currency")
    search_fields = ("base_currency__symbol", "quote_currency__symbol")
    ordering = ("-timestamp",)


@admin.register(Account)
class AccountAdmin(BaseSerializableAdmin):
    list_display = ("name", "owner_display", "manager", "currency", "balance", "active", "created_at")
    search_fields = ("name", "owner__name")
    list_filter = ("currency", "active")
    readonly_fields = ("created_at",)

    def owner_display(self, obj):
        """
        Show the polymorphic identity of the owner (CommunityMember, Company, etc.)
        """
        if obj.owner:
            real_instance = obj.owner.get_real_instance_class()
            return f"{real_instance.__name__}: id={obj.owner.id}"
        return "-"
    owner_display.short_description = "Owner"


@admin.register(Transaction)
class TransactionAdmin(BaseSerializableAdmin):
    list_display = ("name", "amount", "currency", "source", "destination", "timestamp")
    search_fields = ("name", "source__name", "destination__name")
    list_filter = ("currency", "timestamp")
    readonly_fields = ("timestamp",)
    actions = ["execute_transactions"]

    @admin.action(description="Execute selected transactions")
    def execute_transactions(self, request, queryset):
        def execute_one(tx):
            tx.execute()
            return tx

        result = BatchAction(queryset).run(execute_one)
        BatchAction.display_messages(result, self.message_user, request, verb="execute")
