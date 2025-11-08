from django.contrib import admin
from .models import Currency, Subject, Account, Transaction, Obligation, ExchangeRate
from community.models import SocialEntity


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


@admin.register(Subject)
class SubjectAdmin(admin.ModelAdmin):
    list_display = ("name", "legal_type", "identifier", "linked_entity", "active", "created_at")
    search_fields = ("name", "identifier", "social_entity__name")
    list_filter = ("legal_type", "active")
    readonly_fields = ("created_at",)

    def linked_entity(self, obj):
        if hasattr(obj.social_entity, "community"):
            return f"Community: {obj.social_entity.community.name}"
        elif hasattr(obj.social_entity, "member"):
            return f"Member: {obj.social_entity.member.display_name}"
        return "-"
    linked_entity.short_description = "Linked SocialEntity"


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "manager", "currency", "balance", "active", "created_at")
    search_fields = ("name", "owner__name")
    list_filter = ("currency", "active")
    readonly_fields = ("created_at",)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ("name", "amount", "currency", "source", "destination", "timestamp")
    search_fields = ("name",)
    list_filter = ("currency", "timestamp")
    readonly_fields = ("timestamp",)


@admin.register(Obligation)
class ObligationAdmin(admin.ModelAdmin):
    list_display = ("name", "amount", "currency", "source", "destination", "due_date", "fulfilled", "timestamp")
    search_fields = ("name",)
    list_filter = ("currency", "fulfilled", "due_date")
    readonly_fields = ("timestamp",)
