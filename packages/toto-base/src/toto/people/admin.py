from django import forms
from django.contrib import admin

from toto.core.base_admin import TotoModelAdmin

from .models import Person


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
