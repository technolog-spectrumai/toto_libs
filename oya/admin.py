from django.contrib import admin
from .models import (Platform, DashboardBlock, Font, Theme, Company, CommunityMember, Address, MembershipApplication,
                     Branch, ReferenceRequest)


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('street', 'building', 'apartment', 'locality_name', 'state_or_province_name', 'country_name')
    search_fields = ('street', 'locality_name', 'state_or_province_name', 'country_name')
    list_filter = ('country_name', 'state_or_province_name')
    ordering = ('locality_name', 'street')

@admin.register(Font)
class FontAdmin(admin.ModelAdmin):
    list_display = ('name', 'style_family', 'cdn_link')
    list_filter = ('style_family',)
    search_fields = ('name', 'cdn_link')
    ordering = ('name',)


@admin.register(Theme)
class ThemeAdmin(admin.ModelAdmin):
    list_display = ('name', 'font')
    search_fields = ('name', 'font__name')
    ordering = ('name',)


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ('name', 'address', 'established_year', 'created_at')
    search_fields = ('name',)
    ordering = ('name',)


@admin.register(Platform)
class PlatformAdmin(admin.ModelAdmin):
    list_display = ('site_name', 'get_company_name', 'domain', 'publication_year', 'active', 'theme')
    search_fields = ('site_name', 'company__name', 'theme__name')
    list_filter = ('active', 'publication_year', 'theme')
    ordering = ['publication_year']

    def get_company_name(self, obj):
        return obj.company.name if obj.company else '-'
    get_company_name.short_description = 'Company Name'


@admin.register(DashboardBlock)
class DashboardBlockAdmin(admin.ModelAdmin):
    list_display = ('title', 'icon', 'description', 'link')
    search_fields = ('title', 'description', 'icon', 'link')
    ordering = ('title',)

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'platform', 'address', 'created_at', 'updated_at')
    search_fields = ('name', 'platform__site_name', 'platform__company__name')
    list_filter = ('platform__company', 'created_at')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')

@admin.register(CommunityMember)
class CommunityMemberAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'joined_date')
    search_fields = ('display_name', 'user__username', 'user__email')
    list_filter = ('joined_date',)
    ordering = ('-joined_date',)
    filter_horizontal = ('membership',)


@admin.register(MembershipApplication)
class MembershipApplicationAdmin(admin.ModelAdmin):
    list_display = ('email', 'code', 'branch', 'status', 'is_verified_display', 'expires_at')
    list_filter = ('branch', 'status', 'expires_at')
    search_fields = ('email', 'code', 'branch__name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'verified_at')

    @admin.display(boolean=True, description='Verified')
    def is_verified_display(self, obj):
        return obj.is_verified

@admin.register(ReferenceRequest)
class ReferenceRequestAdmin(admin.ModelAdmin):
    list_display = (
        'application',
        'referrer',
        'status',
        'created_at',
        'responded_at',
        'is_accepted_display'
    )
    list_filter = (
        'status',
        'created_at',
        'responded_at'
    )
    search_fields = (
        'application__email',
        'referrer__display_name'
    )
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'responded_at')

    @admin.display(boolean=True, description='Accepted')
    def is_accepted_display(self, obj):
        return obj.is_accepted



