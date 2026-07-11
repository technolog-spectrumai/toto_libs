from django.contrib import admin
from toto.core.base_admin import TotoModelAdmin
from .models import Travel, Visit


@admin.register(Travel)
class TravelAdmin(TotoModelAdmin):
    list_display = ("id", "route", "starts_at", "ends_at", "participant_count")
    list_display_links = ("id", "route")
    search_fields = (
        "info",
        "route__name",
        "participants__display_name",
        "participants__email",
    )
    list_filter = ("starts_at", "ends_at")
    autocomplete_fields = ("route",)
    filter_horizontal = ("participants",)
    ordering = ("-starts_at",)

    def participant_count(self, obj):
        return obj.participants.count()

    participant_count.short_description = "Participants"


@admin.register(Visit)
class VisitAdmin(TotoModelAdmin):
    list_display = ("id", "participant", "location", "score")
    list_display_links = ("id", "participant")
    list_filter = ("score",)
    search_fields = (
        "participant__display_name",
        "participant__email",
        "location__street",
        "location__locality_name",
        "location__state_or_province_name",
        "location__country_name",
        "review",
    )
    autocomplete_fields = ("participant", "location")
    ordering = ("participant", "location")
