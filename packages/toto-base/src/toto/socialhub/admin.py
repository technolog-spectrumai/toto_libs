from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.utils.translation import gettext_lazy as _

from toto.core.base_admin import TotoModelAdmin
from toto.people.models import Person
from toto.verbena.admin import make_section_form

from .models import (
    Clearance,
    Community,
    CommunityNewsPost,
    CommunityNewsTopic,
    CommunityPrivilege,
    MembershipApplication,
    PrivacyAcceptance,
    ReferenceRequest,
)


class CommunityPrivilegeInline(admin.StackedInline):
    """The grant, editable where the community is edited."""
    model = CommunityPrivilege
    can_delete = True
    extra = 0


class ClearanceAdminForm(forms.ModelForm):
    """Who holds a clearance, edited on the clearance's own page (2026-09-28).

    A clearance is given through the admin, the Clearances tab or the console
    and nowhere else, so its page lists its holders. Written through
    ``clearance.members.set`` — the relation's own door, so a change here is
    the same ``m2m_changed`` as one made on the person.
    """

    members = forms.ModelMultipleChoiceField(
        queryset=Person.objects.order_by("display_name"),
        required=False,
        label=_("Members"),
        widget=FilteredSelectMultiple(_("Members"), is_stacked=False),
    )

    class Meta:
        model = Clearance
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["members"].initial = self.instance.members.all()

    def _save_m2m(self):
        super()._save_m2m()
        self.instance.members.set(self.cleaned_data["members"])


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
    )

    ordering = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('senior_members',)
    autocomplete_fields = ('parent',)
    inlines = (CommunityPrivilegeInline,)

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = _("Head of Community")

    # --- FIX: expose property cleanly in admin ---
    def is_foreign_display(self, obj):
        return obj.is_foreign
    is_foreign_display.boolean = True
    is_foreign_display.short_description = "Foreign"


def _manages_clearances(user) -> bool:
    """The Clearances tab's rule (``views.clearances.may_manage``): a real
    superuser on the Superuser plan."""
    from toto.socialhub.views.clearances import may_manage

    return may_manage(user)


@admin.register(Clearance)
class ClearanceAdmin(TotoModelAdmin):
    """Superusers only (2026-09-29, the owner's rule): a clearance decides who
    reads and how fast mana refills, so no staff right reaches this page —
    not to see it, not to add, change or delete. The Clearances tab in the
    socialhub is the same door with fewer fields, and its rule is this
    page's (2026-10-02, the crown bug hunt): a superuser on the Superuser
    plan. The superuser bit alone let one off the plan make clearances, give
    them and delete them here while the tab refused them."""

    form = ClearanceAdminForm
    list_display = ("name", "slug", "regen_security", "regen_compute", "regen_storage", "holders")
    search_fields = ("name", "slug")
    ordering = ("name",)
    prepopulated_fields = {"slug": ("name",)}

    def holders(self, obj):
        return obj.members.count()
    holders.short_description = _("Holders")

    def has_module_permission(self, request):
        return _manages_clearances(request.user)

    def has_view_permission(self, request, obj=None):
        return _manages_clearances(request.user)

    def has_add_permission(self, request):
        return _manages_clearances(request.user)

    def has_change_permission(self, request, obj=None):
        return _manages_clearances(request.user)

    def has_delete_permission(self, request, obj=None):
        return _manages_clearances(request.user)



@admin.register(MembershipApplication)
class MembershipApplicationAdmin(TotoModelAdmin):
    list_display = ('email', 'code', 'community', 'status', 'is_verified_display', 'privacy_version',
                    'expires_at')
    list_filter = ('community', 'status', 'privacy_version', 'expires_at')
    search_fields = ('email', 'code', 'community__name')
    ordering = ('-created_at',)
    # What the applicant accepted is a record, not an editable field (2026-10-01).
    readonly_fields = ('created_at', 'privacy_version', 'privacy_accepted_at')

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



@admin.register(PrivacyAcceptance)
class PrivacyAcceptanceAdmin(TotoModelAdmin):
    """Who accepted which privacy notice (2026-10-01). Read-only: a row is
    written on admission from the application and never by hand."""
    list_display = ('person', 'version', 'accepted_at')
    list_filter = ('version',)
    search_fields = ('person__display_name', 'person__user__username')
    ordering = ('-accepted_at',)
    readonly_fields = ('person', 'version', 'accepted_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
