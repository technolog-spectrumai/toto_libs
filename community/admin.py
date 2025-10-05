from django.contrib import admin
from django.utils.html import format_html
from django.utils import timezone
from .models import (
    Company,
    CommunityMember,
    Address,
    MembershipApplication,
    Branch,
    ReferenceRequest
)

# Inline for displaying branches under a company
class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('street', 'building', 'apartment', 'locality_name', 'state_or_province_name', 'country_name')
    search_fields = ('street', 'locality_name', 'state_or_province_name', 'country_name')
    list_filter = ('country_name', 'state_or_province_name')
    ordering = ('locality_name', 'street')


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'slug',
        'address',
        'established_year',
        'head_display',
        'created_at',
    )
    search_fields = ('name', 'slug', 'head__display_name')
    ordering = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    inlines = [BranchInline]

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = "Head of Company"


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('name', 'company', 'address', 'head_display', 'created_at', 'updated_at')
    search_fields = ('name', 'company__name', 'address__locality_name', 'address__street', 'head__display_name')
    list_filter = ('company', 'created_at')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'updated_at')

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = "Head of Branch"


@admin.register(CommunityMember)
class CommunityMemberAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'patron_display', 'joined_date', 'avatar_preview', 'slug')
    search_fields = ('display_name', 'user__username', 'user__email', 'patron__display_name')
    list_filter = ('joined_date',)
    ordering = ('-joined_date',)
    filter_horizontal = ('membership',)

    def avatar_preview(self, obj):
        if obj.avatar:
            return format_html('<img src="{}" style="height: 40px; border-radius: 4px;" />', obj.avatar.url)
        return "-"
    avatar_preview.short_description = "Avatar"

    def patron_display(self, obj):
        return obj.patron.display_name if obj.patron else "-"
    patron_display.short_description = "Patron"


# Custom action for verifying applications
@admin.action(description='Mark selected applications as verified')
def mark_as_verified(modeladmin, request, queryset):
    queryset.update(status='verified', verified_at=timezone.now())


@admin.register(MembershipApplication)
class MembershipApplicationAdmin(admin.ModelAdmin):
    list_display = ('email', 'code', 'branch', 'status', 'is_verified_display', 'expires_at')
    list_filter = ('branch', 'status', 'expires_at')
    search_fields = ('email', 'code', 'branch__name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at', 'verified_at')
    actions = [mark_as_verified]

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