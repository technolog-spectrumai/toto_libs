from django.contrib import admin

from toto.events.models import Availability, EventCategory, EventInvite, ScheduledEvent


@admin.register(EventCategory)
class EventCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "description")
    search_fields = ("name", "description")


@admin.register(ScheduledEvent)
class ScheduledEventAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "owner",
        "category",
        "address",
        "start_time",
        "end_time",
        "requires_registration",
        "public",
    )
    list_filter = ("category", "public", "requires_registration", "start_time")
    search_fields = (
        "title",
        "description",
        "owner__display_name",
        "owner__email",
        "category__name",
        "address__street",
        "address__locality_name",
    )
    autocomplete_fields = ("owner", "organizers", "category", "address")
    date_hierarchy = "start_time"
    ordering = ("-start_time", "title")
    fieldsets = (
        (
            "Event",
            {"fields": ("title", "description", "category", "public")},
        ),
        (
            "Time",
            {"fields": ("start_time", "end_time")},
        ),
        (
            "People",
            {"fields": ("owner", "organizers")},
        ),
        (
            "Location & Registration",
            {"fields": ("address", "requires_registration", "capacity")},
        ),
    )


@admin.register(EventInvite)
class EventInviteAdmin(admin.ModelAdmin):
    list_display = ("person", "event", "status", "sent_at", "responded_at")
    list_filter = ("status",)
    search_fields = ("person__display_name", "event__title")
    autocomplete_fields = ("person", "event")
    ordering = ("-sent_at",)
    readonly_fields = ("sent_at", "responded_at")


@admin.register(Availability)
class AvailabilityAdmin(admin.ModelAdmin):
    list_display = (
        "person",
        "availability_type",
        "start_time",
        "end_time",
        "blocks_scheduling",
        "reason",
    )
    list_filter = ("availability_type", "blocks_scheduling")
    search_fields = ("person__display_name", "person__email", "reason")
    autocomplete_fields = ("person",)
    date_hierarchy = "start_time"
    ordering = ("-start_time",)
