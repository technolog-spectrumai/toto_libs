from django.contrib import admin

from .models import BentoCategory, BentoEdgeType


@admin.register(BentoCategory)
class BentoCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "neo4j_label", "updated_at")
    search_fields = ("name", "slug", "neo4j_label")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "neo4j_label", "description")}),
        ("Schema", {"fields": ("property_schema",)}),
        ("Ingestor detection", {
            "fields": ("searchable_properties", "alias_property"),
            "classes": ("collapse",),
        }),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )


@admin.register(BentoEdgeType)
class BentoEdgeTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "rel_type", "directed", "updated_at")
    search_fields = ("name", "slug", "rel_type")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("allowed_sources", "allowed_targets")
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        (None, {"fields": ("name", "slug", "rel_type", "directed", "description")}),
        ("Allowed endpoints", {"fields": ("allowed_sources", "allowed_targets")}),
        ("Schema", {"fields": ("property_schema",)}),
        ("Ingestor detection", {"fields": ("trigger_lemmas",), "classes": ("collapse",)}),
        ("Timestamps", {"fields": ("created_at", "updated_at"), "classes": ("collapse",)}),
    )
