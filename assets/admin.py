from django.contrib import admin
from .models import Asset, AssetType


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at",)
    search_fields = ("name",)
    ordering = ("name",)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "asset_type",
        "serial_number",
        "assigned_to",
        "location",
        "is_active",
        "purchase_date",
        "purchase_price",
        "created_at",
    )
    list_filter = ("asset_type", "is_active", "purchase_date")
    search_fields = ("name", "serial_number", "location", "assigned_to__username")
    ordering = ("name",)
    autocomplete_fields = ("asset_type", "assigned_to")
    readonly_fields = ("created_at",)

    fieldsets = (
        (None, {
            "fields": ("name", "asset_type", "description", "metadata")
        }),
        ("Identification", {
            "fields": ("serial_number", "location")
        }),
        ("Ownership", {
            "fields": ("assigned_to", "is_active")
        }),
        ("Purchase Info", {
            "fields": ("purchase_date", "purchase_price")
        }),
        ("Timestamps", {
            "fields": ("created_at",)
        }),
    )
