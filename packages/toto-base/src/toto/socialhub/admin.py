from django import forms
from django.contrib import admin
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.core.exceptions import ValidationError
from django.forms.models import BaseInlineFormSet
from django.utils.translation import gettext_lazy as _

from toto.core.base_admin import TotoModelAdmin
from toto.people.models import Person
from toto.verbena.admin import make_section_form

from .models import (
    CIRCLE_GRANTS_NO_PRIVILEGE,
    Community,
    CommunityForum,
    CommunityNewsPost,
    CommunityNewsTopic,
    CommunityPrivilege,
    MembershipApplication,
    ReferenceRequest,
)


class CommunityPrivilegeFormSet(BaseInlineFormSet):
    """A circle grants nothing. The community on this page may be becoming a
    circle in the very request that adds its grant, before either is saved —
    so the formset reads the posted community, which the model cannot yet."""

    def clean(self):
        super().clean()
        if not getattr(self.instance, "is_circle", False):
            return
        for form in self.forms:
            data = getattr(form, "cleaned_data", None) or {}
            if data and not data.get("DELETE") and (form.instance.pk or form.has_changed()):
                raise ValidationError(CIRCLE_GRANTS_NO_PRIVILEGE)


class CommunityPrivilegeInline(admin.StackedInline):
    """The grant, editable where the community is edited — for a functional
    community only; `CommunityAdmin.get_inline_instances` leaves it off a
    circle's page."""
    model = CommunityPrivilege
    formset = CommunityPrivilegeFormSet
    can_delete = True
    extra = 0


class CircleAdminForm(forms.ModelForm):
    """Who is in a circle, edited on the circle's own page (2026-09-28).

    A circle is joined through the admin and nowhere else, so its page lists
    its members. Written through ``community.members.set`` — the relation's
    own door, so a change here is the same ``m2m_changed`` as one made on the
    person. A functional community has no such field: members come in by
    application, and a person's communities are edited on the person.
    """

    members = forms.ModelMultipleChoiceField(
        queryset=Person.objects.order_by("display_name"),
        required=False,
        label=_("Members"),
        widget=FilteredSelectMultiple(_("Members"), is_stacked=False),
    )

    class Meta:
        model = Community
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
        'is_circle',
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
        'is_circle',
        'org_type',
        'established_year',
        'location',
    )

    ordering = ('name',)
    prepopulated_fields = {'slug': ('name',)}
    filter_horizontal = ('senior_members',)
    autocomplete_fields = ('parent',)
    inlines = (CommunityPrivilegeInline,)

    #: Only a superuser makes a circle, or turns a community into one, or
    #: sets its refill speeds (2026-09-29, the owner's rule) — the Circles
    #: tab is theirs and so is this page. A staff member with the add or
    #: change right on communities makes functional ones alone.
    CIRCLE_ONLY_FIELDS = ("is_circle", "regen_security", "regen_compute", "regen_storage")

    def get_form(self, request, obj=None, change=False, **kwargs):
        # A circle's page carries its members; see CircleAdminForm.
        if obj is not None and obj.is_circle and self.has_change_permission(request, obj):
            kwargs["form"] = CircleAdminForm
        return super().get_form(request, obj, change=change, **kwargs)

    def get_queryset(self, request):
        # Circles are not a staff member's to see here either.
        qs = super().get_queryset(request)
        return qs if request.user.is_superuser else qs.functional()

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields += self.CIRCLE_ONLY_FIELDS
        return fields

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.is_circle and not request.user.is_superuser:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.is_circle and not request.user.is_superuser:
            return False
        return super().has_delete_permission(request, obj)

    def save_model(self, request, obj, form, change):
        # The read-only fields keep a crafted POST out already; this is the
        # second layer, for a route that bypasses the form.
        if not request.user.is_superuser and (obj.is_circle or obj.regen_speeds()):
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Only a superuser makes a circle.")
        super().save_model(request, obj, form, change)

    def get_inline_instances(self, request, obj=None):
        # A circle grants nothing, so its page offers no grant to fill in.
        inlines = super().get_inline_instances(request, obj)
        if obj is not None and obj.is_circle:
            inlines = [inline for inline in inlines
                       if not isinstance(inline, CommunityPrivilegeInline)]
        return inlines

    def head_display(self, obj):
        return obj.head.display_name if obj.head else "-"
    head_display.short_description = _("Head of Community")

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



@admin.register(CommunityForum)
class CommunityForumAdmin(admin.ModelAdmin):
    """One room per community. `channel_slug` is a SLUG, not a foreign key —
    see the model docstring for why — so it is typed rather than picked, and
    nothing here checks that the room exists. A slug naming no room renders
    the panel's "no room yet" state on the community page, which is the
    designed behaviour and not a validation gap to close here."""

    list_display = ("community", "channel_slug", "created_at")
    search_fields = ("community__name", "channel_slug")
