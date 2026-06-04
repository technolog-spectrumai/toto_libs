from django.contrib import admin

from toto.inventory.models import (
    InventorySite,
    ObjectType,
    RealWorldObject,
    StorageLocation,
)


class StorageLocationInline(admin.TabularInline):
    model = StorageLocation
    extra = 0
    fields = ("name", "code", "location_type", "is_active")


@admin.register(ObjectType)
class ObjectTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "is_mobile", "slug", "created_at")
    list_filter = ("category", "is_mobile")
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(RealWorldObject)
class RealWorldObjectAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "object_type",
        "owner",
        "custodian",
        "verified_by",
        "quantity",
        "unit",
        "estimated_value",
        "estimated_value_currency",
        "verified_at",
        "created_at",
    )
    list_filter = (
        "object_type",
        "estimated_value_currency",
        "verified_at",
        "created_at",
    )
    search_fields = (
        "name",
        "slug",
        "description",
        "owner__name",
        "custodian__name",
        "verified_by__name",
    )
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at",)


@admin.register(InventorySite)
class InventorySiteAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "slug",
        "site_type",
        "operator",
        "address",
        "is_active",
        "is_virtual",
        "created_at",
    )
    list_filter = ("site_type", "is_active", "is_virtual", "created_at")
    search_fields = ("name", "slug", "site_type", "operator__name")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at",)
    inlines = [StorageLocationInline]


@admin.register(StorageLocation)
class StorageLocationAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "code",
        "site",
        "location_type",
        "is_active",
        "created_at",
    )
    list_filter = ("site", "location_type", "is_active", "created_at")
    search_fields = ("name", "code", "site__name", "location_type")
    readonly_fields = ("created_at",)
