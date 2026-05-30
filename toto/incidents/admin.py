from django.contrib import admin
from .models import Incident, IncidentCategory


@admin.register(IncidentCategory)
class IncidentCategoryAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active"]
    prepopulated_fields = {"slug": ("name",)}
    list_filter = ["is_active"]


@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ["title", "severity", "incident_type", "status", "location_label", "confirmed_at", "created_at"]
    list_filter = ["status", "severity", "incident_type"]
    search_fields = ["title", "description"]
    readonly_fields = ["id", "created_at", "updated_at", "source_detection", "confirmed_at", "confirmed_by"]
    raw_id_fields = ["address", "zone", "reported_by"]

    def location_label(self, obj):
        return obj.location_label
    location_label.short_description = "Location"
