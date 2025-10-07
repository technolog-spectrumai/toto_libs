from django.contrib import admin
from .models import (
    Chamber, Investor, Associate, Currency,
    Venture, Transaction, Event
)

@admin.register(Chamber)
class ChamberAdmin(admin.ModelAdmin):
    list_display = ('name', 'active', 'created_at')
    list_filter = ('active',)
    search_fields = ('name',)


@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'chamber', 'balance', 'kyc_verified', 'joined_at')
    list_filter = ('kyc_verified', 'chamber')
    search_fields = ('display_name', 'wallet_address', 'user__username')


@admin.register(Associate)
class AssociateAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'role', 'active', 'joined_at')
    list_filter = ('active',)
    search_fields = ('display_name', 'role')


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


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'event_type', 'severity', 'start', 'end', 'owner')
    list_filter = ('event_type', 'severity', 'start')
    search_fields = ('title', 'owner__display_name', 'event_type')
    fieldsets = (
        (None, {
            'fields': ('title', 'description', 'event_type', 'severity', 'start', 'end', 'owner', 'metadata')
        }),
    )
