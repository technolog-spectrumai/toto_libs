from django.contrib import admin
from .models import (
    Chamber, Investor, Currency,
    Venture, Transaction
)
from decimal import Decimal


@admin.register(Chamber)
class ChamberAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'created_at', 'total_stock_emitted')
    list_filter = ('active',)
    search_fields = ('name',)


@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = (
        'display_name',
        'user',
        'chamber',
        'stock_owned',
        'ownership_percent_display',
        'joined_at',
    )
    list_filter = ('chamber',)
    search_fields = ('display_name', 'user__username')

    def ownership_percent_display(self, obj):
        total_stock = obj.chamber.total_stock_emitted or Decimal('0')
        stock_owned = obj.stock_owned or Decimal('0')

        if total_stock > 0:
            percent = (stock_owned / total_stock) * Decimal('100')
            return f"{percent:.2f}%"
        return "0.00%"

    ownership_percent_display.short_description = "Ownership %"


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'name', 'is_crypto', 'decimals', 'active')
    list_filter = ('is_crypto', 'active')
    search_fields = ('symbol', 'name')


@admin.register(Venture)
class VentureAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'start', 'end')
    search_fields = ('name',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'currency', 'venture', 'timestamp')
    list_filter = ('currency', 'timestamp')
    search_fields = ('name', 'venture__name')
