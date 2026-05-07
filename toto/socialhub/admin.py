from django.contrib import admin
from .models import (
    Community,
    Experience, Person, MembershipApplication, ReferenceRequest, EmailService
)
from toto.core.base_admin import TotoModelAdmin


@admin.register(Community)
class CommunityAdmin(TotoModelAdmin):
    list_display = (
        'name',
        'slug',
        'org_type',
        'established_year',
        'head_display',
        'email',
        'is_autonomous',
        'is_foreign_display',
    )

    search_fields = (
        'name',
        'slug',
        'head__display_name',
        'org_type',
    )

    list_filter = (
        'org_type',
        'established_year',
        'location',
    )

    ordering = ('name',)
    prepopulated_fields = {'slug': ('name',)}

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = "Head of Community"

    # --- FIX: expose property cleanly in admin ---
    def is_foreign_display(self, obj):
        return obj.is_foreign
    is_foreign_display.boolean = True
    is_foreign_display.short_description = "Foreign"


class ExperienceInline(admin.TabularInline):
    model = Experience
    extra = 0
    fields = (
        "title",
        "institution",
        "place",
        "started_at",
        "ended_at",
        "is_current",
        "order",
    )
    ordering = (
        "order",
        "-started_at",
    )


@admin.register(Person)
class PersonAdmin(TotoModelAdmin):
    list_display = (
        'display_name',
        'user',
        'patron_display',
        'joined_date',
        'slug',
        'id',
        'address_display',
        'email',
    )
    search_fields = (
        'display_name',
        'user__username',
        'user__email',
        'email',
        'patron__display_name',
        'address__street',
        'address__locality_name',
        'address__state_or_province_name',
        'address__country_name',
    )
    list_filter = ('joined_date', 'address__country_name', 'address__state_or_province_name')
    ordering = ('-joined_date',)
    filter_horizontal = ('communities',)
    inlines = [
        ExperienceInline,
    ]

    def patron_display(self, obj):
        return obj.patron.display_name if obj.patron else "-"
    patron_display.short_description = "Patron"

    def address_display(self, obj):
        return str(obj.address) if obj.address else "-"
    address_display.short_description = "Address"


@admin.register(Experience)
class ExperienceAdmin(TotoModelAdmin):
    list_display = (
        "title",
        "person",
        "institution",
        "place",
        "started_at",
        "ended_at",
        "is_current",
        "order",
    )
    list_filter = (
        "is_current",
        "institution",
        "started_at",
    )
    search_fields = (
        "title",
        "institution",
        "place",
        "description",
        "person__display_name",
    )
    autocomplete_fields = (
        "person",
    )


@admin.register(MembershipApplication)
class MembershipApplicationAdmin(TotoModelAdmin):
    list_display = ('email', 'code', 'community', 'status', 'is_verified_display', 'expires_at')
    list_filter = ('community', 'status', 'expires_at')
    search_fields = ('email', 'code', 'community__name')
    ordering = ('-created_at',)
    readonly_fields = ('created_at',)

    @admin.display(boolean=True, description='Verified')
    def is_verified_display(self, obj):
        return obj.is_verified


@admin.register(ReferenceRequest)
class ReferenceRequestAdmin(TotoModelAdmin):
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


@admin.register(EmailService)
class EmailServiceAdmin(TotoModelAdmin):

    list_display = (
        "name",
        "email_address",
        "host",
        "port",
        "created_at",
        "pass_phrase_env_var",
    )

    readonly_fields = ("created_at",)
