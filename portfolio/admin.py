from django.contrib import admin
from .models import (
    Currency,
    Company,
    Shareholder,
    Venture,
    Transaction,
    Chamber
)

from yamabiko.admin import BaseSerializableAdmin

@admin.register(Chamber)
class ChamberAdmin(BaseSerializableAdmin):
    list_display = ('name', 'default_currency', 'active')
    list_filter = ('active', 'default_currency')
    search_fields = ('name',)

@admin.register(Currency)
class CurrencyAdmin(BaseSerializableAdmin):
    list_display = ('symbol', 'name', 'is_crypto', 'decimals', 'active')
    list_filter = ('is_crypto', 'active')
    search_fields = ('symbol', 'name')


@admin.register(Company)
class CompanyAdmin(BaseSerializableAdmin):
    list_display = ('name', 'registration_number', 'country', 'industry', 'date_founded', 'is_active')
    list_filter = ('country', 'industry', 'is_active')
    search_fields = ('name', 'registration_number')


@admin.register(Shareholder)
class ShareholderAdmin(BaseSerializableAdmin):
    list_display = ('full_name', 'email', 'shares_owned', 'company', 'date_joined', 'is_active')
    list_filter = ('is_active', 'company')
    search_fields = ('full_name', 'email', 'company__name')


@admin.register(Venture)
class VentureAdmin(BaseSerializableAdmin):
    list_display = ('name', 'url', 'start', 'end', 'company')
    search_fields = ('name', 'company__name')


@admin.register(Transaction)
class TransactionAdmin(BaseSerializableAdmin):
    list_display = ('name', 'amount', 'currency', 'venture', 'timestamp')
    list_filter = ('currency', 'timestamp')
    search_fields = ('name', 'venture__name')
