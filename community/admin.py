from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Community,
    CommunityMember,
    Address,
    MembershipApplication,
    ReferenceRequest
)
from toto.admin import BaseSerializableAdmin


@admin.register(Address)
class AddressAdmin(BaseSerializableAdmin):
    list_display = ('street', 'building', 'apartment', 'locality_name', 'state_or_province_name', 'country_name')
    search_fields = ('street', 'locality_name', 'state_or_province_name', 'country_name')
    list_filter = ('country_name', 'state_or_province_name')
    ordering = ('locality_name', 'street')


@admin.register(Community)
class CommunityAdmin(BaseSerializableAdmin):
    list_display = (
        'name',
        'slug',
        'location',
        'established_year',
        'head_display',
        'created_at',
        'id', 'email'
    )
    search_fields = ('name', 'slug', 'head__display_name')
    ordering = ('name',)
    prepopulated_fields = {'slug': ('name',)}

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = "Head of Community"


@admin.register(CommunityMember)
class CommunityMemberAdmin(BaseSerializableAdmin):
    list_display = ('display_name', 'user', 'patron_display', 'joined_date', 'avatar_preview', 'slug', 'id')
    search_fields = ('display_name', 'user__username', 'user__email', 'patron__display_name')
    list_filter = ('joined_date',)
    ordering = ('-joined_date',)
    filter_horizontal = ('communities',)

    def avatar_preview(self, obj):
        if obj.avatar:
            return format_html('<img src="{}" style="height: 40px; border-radius: 4px;" />', obj.avatar.url)
        return "-"
    avatar_preview.short_description = "Avatar"

    def patron_display(self, obj):
        return obj.patron.display_name if obj.patron else "-"
    patron_display.short_description = "Patron"


@admin.register(MembershipApplication)
class MembershipApplicationAdmin(admin.ModelAdmin):
    list_display = ('email', 'code', 'community', 'status', 'is_verified_display', 'expires_at')
    list_filter = ('community', 'status', 'expires_at')
    search_fields = ('email', 'code', 'community__name')
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
    list_filter = ('status', 'created_at', 'responded_at')
    search_fields = ('application__email', 'referrer__display_name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'responded_at')

    @admin.display(boolean=True, description='Accepted')
    def is_accepted_display(self, obj):
        return obj.is_accepted
