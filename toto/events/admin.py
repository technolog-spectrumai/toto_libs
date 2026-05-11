from django.contrib import admin

from toto.events.models import Event, EventCategory


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "description",
    )
    search_fields = (
        "name",
        "description",
    )


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "organizer",
        "category",
        "location_display",
        "start_time",
        "end_time",
        "public",
    )

    list_filter = (
        "category",
        "public",
        "start_time",
        "end_time",
        "address",
        "route",
        "zone",
    )

    search_fields = (
        "title",
        "description",
        "organizer__display_name",
        "organizer__email",
        "category__name",
        "address__street",
        "address__building",
        "address__locality_name",
        "route__name",
        "zone__name",
    )

    autocomplete_fields = (
        "organizer",
        "category",
        "address",
        "route",
        "zone",
    )

    date_hierarchy = "start_time"

    ordering = (
        "-start_time",
        "title",
    )

    fieldsets = (
        (
            "Event",
            {
                "fields": (
                    "title",
                    "description",
                    "category",
                    "organizer",
                    "public",
                )
            },
        ),
        (
            "Time",
            {
                "fields": (
                    "start_time",
                    "end_time",
                )
            },
        ),
        (
            "Location",
            {
                "fields": (
                    "address",
                    "route",
                    "zone",
                ),
                "description": (
                    "Use address for fixed-place events, route for movement-based events, "
                    "or zone for broader geographically scoped events."
                ),
            },
        ),
    )

    def location_display(self, obj):
        return obj.effective_location or "—"

    location_display.short_description = "Location"