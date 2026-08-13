from django.contrib import admin

from toto.core.base_admin import TotoModelAdmin
from toto.verbena.admin import make_section_form

from .models import (
    Community,
    CommunityNewsPost,
    CommunityNewsTopic,
    CommunityPrivilege,
    Constitution,
    ConstitutionSignature,
    MembershipApplication,
    ReferenceRequest,
    Station,
)


class CommunityPrivilegeInline(admin.StackedInline):
    """The grant, editable where the community is edited."""
    model = CommunityPrivilege
    can_delete = True
    extra = 0


class StationInline(admin.TabularInline):
    """Offices that serve this community — listed here, paid federally."""
    model = Station
    fk_name = "serves"
    extra = 0
    fields = ("name", "holder", "active", "limit_multiplier", "stipend")
    autocomplete_fields = ("holder",)


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
    inlines = (CommunityPrivilegeInline, StationInline)

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


@admin.register(CommunityPrivilege)
class CommunityPrivilegeAdmin(TotoModelAdmin):
    """The ONLY editor for what communities grant. Deliberately admin-only.

    No page in the product renders or edits privileges — the gates that consume
    them simply work or refuse, and this changelist is where an operator sees
    every grant on the platform at once. A person holds the UNION across their
    communities (highest privilege always), and membership is invite-gated, so
    admitting someone to a listed community IS the grant.
    """

    list_display = ("community", "may_see_community_chain",
                    "may_administer_communities", "may_manage_community_news",
                    "may_operate_mint")
    list_editable = ("may_see_community_chain",
                     "may_administer_communities", "may_manage_community_news",
                     "may_operate_mint")
    list_filter = ("may_see_community_chain",
                   "may_administer_communities", "may_manage_community_news",
                   "may_operate_mint")
    search_fields = ("community__name", "community__slug")
    autocomplete_fields = ("community",)


@admin.register(Station)
class StationAdmin(TotoModelAdmin):
    """The ONLY editor for offices — who holds one, what it grants, what it pays.

    Appointing is setting `holder`; vacating is clearing it; rotation is one
    edit. There is no election and no request workflow: a community that wants
    an office funded asks the federation, and an admin who agrees creates the
    row here.

    Every station is federal however local its work — `serves` says who an
    office works for, never who pays it. The public roster shows the name, the
    charter and the holder; the capabilities and the multiplier are visible only
    here, and so is the stipend — except to its own holder, on their own
    profile, who sees what they are paid and nobody else's.
    """

    list_display = ("name", "serves", "holder", "active", "limit_multiplier",
                    "stipend", "since")
    list_editable = ("holder", "active", "limit_multiplier", "stipend")
    list_filter = ("active", "serves", "may_operate_mint",
                   "may_administer_communities")
    search_fields = ("name", "slug", "charter", "holder__display_name")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("holder", "serves")
