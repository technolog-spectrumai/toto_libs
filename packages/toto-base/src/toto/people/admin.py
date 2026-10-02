import logging

from django import forms
from django.contrib import admin
from django.db import transaction

from toto.core.base_admin import TotoModelAdmin

from .models import Person

log = logging.getLogger(__name__)


def _drop_avatar_files(names) -> None:
    """Delete these avatar files once the change that let them go has
    committed — row first, bytes second, as an erase does it."""
    names = [name for name in names if name]
    if not names:
        return
    storage = Person._meta.get_field("avatar").storage

    def drop():
        for name in names:
            try:
                storage.delete(name)
            except Exception:  # noqa: BLE001 - the row is saved either way
                log.warning("people admin: could not delete the avatar file %s", name)

    transaction.on_commit(drop)


class PersonAdminForm(forms.ModelForm):
    """An avatar set here follows My account's rules (2026-10-01): the four
    formats, the size limits, a name of ours, and the picture drawn again
    without its metadata — a member's photo uploaded by a superuser says
    where it was taken just as much (``toto.socialhub.forms``)."""

    def clean_avatar(self):
        from toto.socialhub.forms import clean_avatar_upload

        return clean_avatar_upload(self.cleaned_data.get("avatar"))


@admin.register(Person)
class PersonAdmin(TotoModelAdmin):
    form = PersonAdminForm
    list_display = ("display_name", "user", "patron_display", "joined_date", "slug", "id", "address_display", "email")
    search_fields = (
        "display_name", "user__username", "user__email", "email",
        "patron__display_name", "address__street",
        "address__locality_name", "address__state_or_province_name", "address__country_name",
    )
    list_filter = ("joined_date", "address__country_name", "address__state_or_province_name")
    ordering = ("-joined_date",)
    filter_horizontal = ("communities",)

    @admin.display(description="Patron")
    def patron_display(self, obj):
        return obj.patron.display_name if obj.patron else "-"

    @admin.display(description="Address")
    def address_display(self, obj):
        return str(obj.address) if obj.address else "-"

    # Who holds a clearance is the Clearances tab's to change (2026-10-02,
    # the crown bug hunt): a superuser on the Superuser plan. Anybody else
    # who may change a person sees the field read-only — it was an ordinary
    # field here, so a staff clerk holding only people.change_person ticked
    # any clearance on their own person and read everything it keeps.
    def get_readonly_fields(self, request, obj=None):
        from toto.socialhub.views.clearances import may_manage

        fields = list(super().get_readonly_fields(request, obj))
        if "clearances" not in fields and not may_manage(request.user):
            fields.append("clearances")
        return fields

    # The picture replaced, taken off or left behind here goes with it
    # (2026-10-01, the review of stage 37c), as on My account: a photo left in
    # /media/ is still a photo of them on the server, at an address anybody
    # who kept it can fetch — and the privacy notice says no earlier avatar
    # is kept.
    def save_model(self, request, obj, form, change):
        old = ""
        if change and "avatar" in form.changed_data:
            old = Person.objects.filter(pk=obj.pk).values_list("avatar", flat=True).first() or ""
        super().save_model(request, obj, form, change)
        if old and old != obj.avatar.name:
            _drop_avatar_files([old])

    def delete_model(self, request, obj):
        name = obj.avatar.name if obj.avatar else ""
        super().delete_model(request, obj)
        _drop_avatar_files([name])

    def delete_queryset(self, request, queryset):
        names = list(queryset.exclude(avatar="").values_list("avatar", flat=True))
        super().delete_queryset(request, queryset)
        _drop_avatar_files(names)
