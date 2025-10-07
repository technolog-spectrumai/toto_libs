from django.contrib import admin
from .models import (
    Investor, Associate, Portfolio, Asset,
    Currency, CurrencyExchangeRate, Transaction,
    Milestone, Event
)

@admin.register(Investor)
class InvestorAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'wallet_address', 'balance', 'kyc_verified', 'joined_at')
    search_fields = ('display_name', 'wallet_address', 'user__username')
    list_filter = ('kyc_verified',)

@admin.register(Associate)
class AssociateAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'role', 'wallet_address', 'active', 'joined_at')
    search_fields = ('display_name', 'role')
    list_filter = ('active',)

@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = ('name', 'investor', 'strategy', 'created_at')
    search_fields = ('name', 'investor__display_name')
    list_filter = ('strategy',)

@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ('symbol', 'name', 'quantity', 'portfolio')
    search_fields = ('symbol', 'name')
    list_filter = ('symbol',)

@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ('name', 'symbol', 'is_crypto', 'is_internal', 'decimals', 'active')
    search_fields = ('name', 'symbol')
    list_filter = ('is_crypto', 'is_internal', 'active')

@admin.register(CurrencyExchangeRate)
class CurrencyExchangeRateAdmin(admin.ModelAdmin):
    list_display = ('from_currency', 'to_currency', 'rate', 'updated_at')
    list_filter = ('from_currency', 'to_currency')
    search_fields = ('from_currency__symbol', 'to_currency__symbol')

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('investor', 'currency', 'amount', 'reason', 'timestamp')
    search_fields = ('investor__display_name', 'reason')
    list_filter = ('currency', 'timestamp')

@admin.register(Milestone)
class MilestoneAdmin(admin.ModelAdmin):
    list_display = ('title', 'portfolio', 'category', 'achieved', 'target_date', 'timestamp')
    search_fields = ('title', 'category')
    list_filter = ('achieved', 'category')

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'owner', 'event_type', 'severity', 'timestamp')
    search_fields = ('title', 'event_type', 'owner__display_name')
    list_filter = ('severity', 'event_type')
