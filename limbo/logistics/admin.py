from django.contrib import admin
from .models import Package, PackageEvent, Transport


class PackageEventInline(admin.TabularInline):
    model = PackageEvent
    extra = 0
    readonly_fields = ("created_at",)
    fields = ("occurred_at", "status", "location", "transport", "note", "created_at")
    raw_id_fields = ("location", "transport")
    ordering = ("occurred_at",)


@admin.register(Transport)
class TransportAdmin(admin.ModelAdmin):
    list_display = ("name", "carrier", "mode", "identifier", "is_active", "current_location")
    list_filter = ("mode", "is_active")
    search_fields = ("name", "carrier", "identifier")
    raw_id_fields = ("current_location", "origin", "destination")


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ("tracking_number", "carrier", "status", "transport", "origin", "destination", "created_at")
    list_filter = ("status",)
    search_fields = ("tracking_number", "carrier", "description")
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("origin", "destination", "transport", "shipment")
    inlines = [PackageEventInline]


@admin.register(PackageEvent)
class PackageEventAdmin(admin.ModelAdmin):
    list_display = ("package", "status", "location", "transport", "occurred_at")
    list_filter = ("status",)
    search_fields = ("package__tracking_number", "note")
    raw_id_fields = ("package", "location", "transport")
