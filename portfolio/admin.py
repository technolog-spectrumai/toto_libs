from django.contrib import admin
from .models import (
    Chamber, Currency,
    Venture, Transaction
)
from decimal import Decimal


@admin.register(Chamber)
class ChamberAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'created_at')
    list_filter = ('active',)
    search_fields = ('name',)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'name', 'is_crypto', 'decimals', 'active')
    list_filter = ('is_crypto', 'active')
    search_fields = ('symbol', 'name')


@admin.register(Venture)
class VentureAdmin(admin.ModelAdmin):
    list_display = ('name', 'url', 'start', 'end', 'company')
    search_fields = ('name',)


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('name', 'amount', 'currency', 'venture', 'timestamp')
    list_filter = ('currency', 'timestamp')
    search_fields = ('name', 'venture__name')
