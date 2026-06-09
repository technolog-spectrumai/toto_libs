from django.contrib import admin

from .models import BentoCategory, BentoEdgeType


@admin.register(BentoCategory)
class BentoCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "neo4j_label", "color", "updated_at")
    search_fields = ("name", "slug", "neo4j_label")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "neo4j_label", "description")}),
        ("Schema", {"fields": ("property_schema",)}),
        ("UI", {"fields": ("color", "icon")}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(BentoEdgeType)
class BentoEdgeTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "rel_type", "directed", "color", "updated_at")
    search_fields = ("name", "slug", "rel_type")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("allowed_sources", "allowed_targets")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "rel_type", "directed", "description")}),
        ("Allowed endpoints", {"fields": ("allowed_sources", "allowed_targets")}),
        ("Schema", {"fields": ("property_schema",)}),
        ("UI", {"fields": ("color",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
