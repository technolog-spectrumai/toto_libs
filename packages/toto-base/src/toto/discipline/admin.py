"""Sigils and auras are made, changed and assigned HERE, in the admin panel,
and nowhere else (the owner, 2026-10-11): the pages list them and change
nothing. Superusers only, whatever model permissions a staff account holds.

Assigning goes through ``sigils.assign`` and ``sigils.revoke``, so a change
made here writes the same history (``SigilEvent``) and the same audit record
as any other: choose a holder on the sigil's form to assign it, clear the
holder to revoke it (or use the action on the list). An aura is given to a
community under "Community auras": one row a community.

Balances and events are read here and written by the services alone.
"""

from django import forms
from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from toto.core.base_admin import TotoModelAdmin

from . import audit, sigils as sigil_services
from .models import Aura, CommunityAura, ManaAccount, ManaEvent, Sigil, SigilEvent


class SuperuserOnly:
    """The admin of Discipline is a superuser's: not listed, read or changed
    by anybody else. ``TOTO_ADMIN_READONLY`` still closes the writes."""

    @staticmethod
    def _superuser(request) -> bool:
        return bool(request.user.is_active and request.user.is_superuser)

    def has_module_permission(self, request):
        return self._superuser(request)

    def has_view_permission(self, request, obj=None):
        return self._superuser(request)

    def has_add_permission(self, request):
        return self._superuser(request) and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return self._superuser(request) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._superuser(request) and super().has_delete_permission(request, obj)


class ReadOnly(SuperuserOnly, admin.ModelAdmin):
    """A record of what happened: read here, written by the services."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ManaAccount)
class ManaAccountAdmin(ReadOnly):
    list_display = ("user", "balance", "updated_at")
    search_fields = ("user__username",)


@admin.register(ManaEvent)
class ManaEventAdmin(ReadOnly):
    list_display = ("created_at", "user", "kind", "delta", "balance_after", "metric_code")
    list_filter = ("kind",)
    search_fields = ("user__username", "key", "metric_code")


@admin.register(SigilEvent)
class SigilEventAdmin(ReadOnly):
    list_display = ("at", "sigil", "action", "user", "by")
    list_filter = ("action",)


#: The largest picture a sigil or an aura takes.
MAX_IMAGE_BYTES = 1024 * 1024


class PictureForm(forms.ModelForm):
    def clean_image(self):
        from django.core.files.uploadedfile import UploadedFile

        image = self.cleaned_data.get("image")
        # Only a new upload is measured: a stored picture is a FieldFile.
        if isinstance(image, UploadedFile) and image.size > MAX_IMAGE_BYTES:
            raise forms.ValidationError(_("The picture is larger than 1 MB."))
        return image


class AuraForm(PictureForm):
    class Meta:
        model = Aura
        fields = ("name", "slug", "description", "regen_per_hour", "cap", "image")
        labels = {"image": _("Picture"), "description": _("Description")}


class SigilForm(PictureForm):
    note = forms.CharField(
        label=_("Note"), required=False, max_length=255,
        help_text=_("Kept in the sigil's history when its holder changes."))

    class Meta:
        model = Sigil
        fields = ("name", "image", "description", "regen_per_hour", "holder")
        labels = {"image": _("Picture"), "description": _("Description"),
                  "holder": _("Holder")}
        help_texts = {
            "holder": _("Choose a person to assign the sigil to them; clear it to revoke the "
                        "sigil. One holder at a time: whoever held it stops holding it."),
            "regen_per_hour": _("What the sigil generates for its holder, on top of their "
                                "community's aura."),
        }


@admin.register(Sigil)
class SigilAdmin(SuperuserOnly, TotoModelAdmin):
    form = SigilForm
    list_display = ("name", "uuid", "regen_per_hour", "holder", "active", "assigned_at")
    list_filter = ("active",)
    search_fields = ("name", "uuid", "holder__username")
    raw_id_fields = ("holder",)
    readonly_fields = ("uuid", "active", "issued_by", "assigned_at", "revoked_by", "revoked_at",
                       "created_at")
    fields = ("name", "image", "description", "regen_per_hour", "holder", "note", "uuid",
              "active", "issued_by", "assigned_at", "revoked_by", "revoked_at", "created_at")
    actions = ("revoke_selected",)

    def save_model(self, request, obj, form, change):
        wanted = form.cleaned_data.get("holder")
        note = form.cleaned_data.get("note") or ""
        before = (Sigil.objects.select_for_update().filter(pk=obj.pk).first()
                  if change else None)
        # The hand changes through the service below, never by this save: what
        # the service owns is written back as it stands, under the row's lock.
        if before is not None:
            for column in ("holder", "active", "assigned_at", "revoked_at", "revoked_by"):
                setattr(obj, column, getattr(before, column))
        else:
            obj.holder = None
            obj.issued_by = request.user
        super().save_model(request, obj, form, change)
        if before is None:
            audit.sigil(obj, "SIGIL_CREATED", by=request.user)
        elif (before.name, before.regen_per_hour) != (obj.name, obj.regen_per_hour):
            audit.sigil(obj, "SIGIL_CHANGED", by=request.user,
                        before={"name": before.name, "rate": str(before.regen_per_hour)})
        held = before.holder if before is not None else None
        if wanted is not None and (wanted != held or not obj.active):
            sigil_services.assign(obj, wanted, by=request.user, note=note)
        elif wanted is None and held is not None:
            sigil_services.revoke(obj, by=request.user, note=note)

    def get_deleted_objects(self, objs, request):
        # A sigil's history goes with it (the audit trail keeps what was done):
        # the history being read-only on its own is no refusal here.
        deleted, counts, perms_needed, protected = super().get_deleted_objects(objs, request)
        perms_needed.discard(SigilEvent._meta.verbose_name)
        return deleted, counts, perms_needed, protected

    def delete_model(self, request, obj):
        audit.sigil(obj, "SIGIL_DELETED", by=request.user, holder=obj.holder)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for sigil in queryset.select_related("holder"):
            audit.sigil(sigil, "SIGIL_DELETED", by=request.user, holder=sigil.holder)
        super().delete_queryset(request, queryset)

    @admin.action(description=_("Revoke the selected sigils"), permissions=["change"])
    def revoke_selected(self, request, queryset):
        count = 0
        for sigil in queryset:
            if sigil.active or sigil.holder_id:
                sigil_services.revoke(sigil, by=request.user)
                count += 1
        self.message_user(request, ngettext(
            "%(count)d sigil revoked. The mana it generated stays where it is.",
            "%(count)d sigils revoked. The mana they generated stays where it is.",
            count) % {"count": count}, messages.SUCCESS)


@admin.register(Aura)
class AuraAdmin(SuperuserOnly, TotoModelAdmin):
    form = AuraForm
    list_display = ("name", "regen_per_hour", "cap", "communities_named")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    fields = ("name", "slug", "description", "regen_per_hour", "cap", "image",
              "communities_named")
    readonly_fields = ("communities_named",)

    @admin.display(description=_("Communities"))
    def communities_named(self, obj):
        if obj is None or not obj.pk:
            return "—"
        names = list(obj.community_links.order_by("community__name")
                     .values_list("community__name", flat=True))
        return ", ".join(names) or "—"

    def get_prepopulated_fields(self, request, obj=None):
        # A slug is given once: other things (the file's seed) name it.
        return {} if obj is not None else super().get_prepopulated_fields(request, obj)

    def get_readonly_fields(self, request, obj=None):
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + ("slug",) if obj is not None and "slug" not in fields else fields

    def save_model(self, request, obj, form, change):
        before = Aura.objects.filter(pk=obj.pk).first() if change else None
        super().save_model(request, obj, form, change)
        if before is None:
            audit.aura(obj, "AURA_CREATED", by=request.user)
        elif (before.name, before.regen_per_hour, before.cap) != (
                obj.name, obj.regen_per_hour, obj.cap):
            audit.aura(obj, "AURA_CHANGED", by=request.user, before={
                "name": before.name, "rate": str(before.regen_per_hour),
                "cap": None if before.cap is None else str(before.cap)})

    def get_deleted_objects(self, objs, request):
        # The communities that have the aura lose it with it.
        deleted, counts, perms_needed, protected = super().get_deleted_objects(objs, request)
        perms_needed.discard(CommunityAura._meta.verbose_name)
        return deleted, counts, perms_needed, protected

    def _record_deletion(self, request, aura):
        for link in aura.community_links.select_related("community"):
            audit.aura_set(link.community, aura, None, by=request.user)
        audit.aura(aura, "AURA_DELETED", by=request.user)

    def delete_model(self, request, obj):
        self._record_deletion(request, obj)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for aura in queryset:
            self._record_deletion(request, aura)
        super().delete_queryset(request, queryset)


@admin.register(CommunityAura)
class CommunityAuraAdmin(SuperuserOnly, TotoModelAdmin):
    """Which aura a community has: one row a community. Several communities
    may name the same aura."""

    list_display = ("community", "aura")
    search_fields = ("community__name", "aura__name")
    autocomplete_fields = ("community", "aura")

    def get_readonly_fields(self, request, obj=None):
        # A row is a community's: to give another community an aura, add a row.
        fields = tuple(super().get_readonly_fields(request, obj))
        return fields + ("community",) if obj is not None and "community" not in fields else fields

    def save_model(self, request, obj, form, change):
        before = (CommunityAura.objects.filter(pk=obj.pk).select_related("aura").first()
                  if change else None)
        super().save_model(request, obj, form, change)
        was = before.aura if before is not None else None
        if was != obj.aura:
            audit.aura_set(obj.community, was, obj.aura, by=request.user)

    def delete_model(self, request, obj):
        audit.aura_set(obj.community, obj.aura, None, by=request.user)
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        for link in queryset.select_related("community", "aura"):
            audit.aura_set(link.community, link.aura, None, by=request.user)
        super().delete_queryset(request, queryset)
