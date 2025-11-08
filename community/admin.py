from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Community,
    CommunityMember,
    Address,
    MembershipApplication,
    ReferenceRequest,
    SocialEntity
)


@admin.register(SocialEntity)
class SocialEntityAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'linked_type', 'linked_name', 'created_at')
    search_fields = ('id', 'name')
    ordering = ('-created_at',)

    def linked_type(self, obj):
        if hasattr(obj, 'community'):
            return "Community"
        elif hasattr(obj, 'member'):
            return "Community Member"
        return "Unlinked"
    linked_type.short_description = "Entity Type"

    def linked_name(self, obj):
        if hasattr(obj, 'community'):
            return obj.community.name
        elif hasattr(obj, 'member'):
            return obj.member.display_name
        return "-"
    linked_name.short_description = "Entity Name"


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('street', 'building', 'apartment', 'locality_name', 'state_or_province_name', 'country_name')
    search_fields = ('street', 'locality_name', 'state_or_province_name', 'country_name')
    list_filter = ('country_name', 'state_or_province_name')
    ordering = ('locality_name', 'street')


@admin.register(Community)
class CommunityAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'slug',
        'address',
        'established_year',
        'head_display',
        'created_at',
        'social_entity_id',
    )
    search_fields = ('name', 'slug', 'head__display_name')
    ordering = ('name',)
    prepopulated_fields = {'slug': ('name',)}

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = "Head of Community"

    def social_entity_id(self, obj):
        return obj.social_entity.id if hasattr(obj, 'social_entity') else "-"
    social_entity_id.short_description = "Social Entity"


@admin.register(CommunityMember)
class CommunityMemberAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'patron_display', 'joined_date', 'avatar_preview', 'slug', 'social_entity_id')
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

    def social_entity_id(self, obj):
        return obj.social_entity.id if hasattr(obj, 'social_entity') else "-"
    social_entity_id.short_description = "Social Entity"



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
