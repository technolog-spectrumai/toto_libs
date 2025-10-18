from django.contrib import admin
from .models import Company, Shareholder, ShareTransaction

@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'registration_number', 'country', 'industry', 'date_founded', 'is_active')
    search_fields = ('name', 'registration_number', 'country', 'industry')
    list_filter = ('country', 'industry', 'is_active')

@admin.register(Shareholder)
class ShareholderAdmin(admin.ModelAdmin):
    list_display = ('full_name', 'email', 'company', 'shares_owned', 'date_joined', 'is_active', 'user')
    search_fields = ('full_name', 'email', 'company__name')
    list_filter = ('company', 'is_active')
    autocomplete_fields = ['company', 'user']

@admin.register(ShareTransaction)
class ShareTransactionAdmin(admin.ModelAdmin):
    list_display = ('shareholder', 'transaction_date', 'shares_changed', 'notes')
    search_fields = ('shareholder__full_name',)
    list_filter = ('transaction_date',)
    date_hierarchy = 'transaction_date'
