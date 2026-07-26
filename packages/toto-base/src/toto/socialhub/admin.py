from django.contrib import admin

from toto.core.base_admin import TotoModelAdmin
from toto.verbena.admin import make_section_form

from .models import (
    Community,
    CommunityNewsPost,
    CommunityNewsTopic,
    Constitution,
    ConstitutionSignature,
    MembershipApplication,
    ReferenceRequest,
)


@admin.register(Community)
class CommunityAdmin(TotoModelAdmin):
    list_display = (
        'name',
        'slug',
        'org_type',
        'established_year',
        'head_display',
        'parent',
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
    filter_horizontal = ('senior_members',)
    autocomplete_fields = ('parent',)

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = "Head of Community"

    # --- FIX: expose property cleanly in admin ---
    def is_foreign_display(self, obj):
        return obj.is_foreign
    is_foreign_display.boolean = True
    is_foreign_display.short_description = "Foreign"



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


@admin.register(CommunityNewsPost)
class CommunityNewsPostAdmin(TotoModelAdmin):
    list_display = (
        "display_title",
        "community",
        "author",
        "created_at",
    )
    list_filter = ("community", "topics", "visibility")
    search_fields = ("title", "content", "community__name", "author__display_name")
    filter_horizontal = ("topics",)
    autocomplete_fields = ("author", "community")
    date_hierarchy = "created_at"

    def get_form(self, request, obj=None, **kwargs):
        kwargs.setdefault("form", make_section_form(CommunityNewsPost))
        return super().get_form(request, obj, **kwargs)


@admin.register(CommunityNewsTopic)
class CommunityNewsTopicAdmin(TotoModelAdmin):
    list_display = ("name", "slug")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class ConstitutionSignatureInline(admin.TabularInline):
    model = ConstitutionSignature
    extra = 0
    fields = ("person", "signed_at", "is_cryptographically_signed_display")
    readonly_fields = ("signed_at", "is_cryptographically_signed_display")

    @admin.display(boolean=True, description="Crypto signed")
    def is_cryptographically_signed_display(self, obj):
        return obj.is_cryptographically_signed


@admin.register(Constitution)
class ConstitutionAdmin(TotoModelAdmin):
    list_display = ("title", "community", "version", "is_active", "signature_count", "created_at")
    list_filter = ("is_active", "community")
    search_fields = ("title", "body", "community__name")
    prepopulated_fields = {"slug": ("title",)}
    raw_id_fields = ("community",)
    readonly_fields = ("created_at", "updated_at")
    inlines = [ConstitutionSignatureInline]

    @admin.display(description="Signatures")
    def signature_count(self, obj):
        return obj.signature_count


@admin.register(ConstitutionSignature)
class ConstitutionSignatureAdmin(TotoModelAdmin):
    list_display = ("person", "constitution", "signed_at", "is_cryptographically_signed_display", "added_at")
    list_filter = ("constitution__community",)
    search_fields = ("person__display_name", "constitution__title")
    raw_id_fields = ("constitution", "person", "signing_key")
    readonly_fields = ("signed_at", "added_at", "signing_payload", "cryptographic_signature")

    @admin.display(boolean=True, description="Crypto signed")
    def is_cryptographically_signed_display(self, obj):
        return obj.is_cryptographically_signed
