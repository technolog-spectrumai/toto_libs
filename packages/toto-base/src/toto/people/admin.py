from django.contrib import admin

from toto.core.base_admin import TotoModelAdmin

from .models import Person


@admin.register(Person)
class PersonAdmin(TotoModelAdmin):
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
