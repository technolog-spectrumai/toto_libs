from django.contrib import admin, messages
from django.utils.html import format_html
from django.urls import reverse

from .models import (
    CypherQuery,
    NodeStyle,
    EdgeStyle,
    DynamicPage,
    FileWorkflow,
    GraphWorkflow,
)

from .style_factory import StyleFactory


# ---------------------------------------------------------
# Inlines
# ---------------------------------------------------------

class NodeStyleInline(admin.TabularInline):
    model = NodeStyle
    extra = 0
    fields = ("collection_type", "color", "size")


class EdgeStyleInline(admin.TabularInline):
    model = EdgeStyle
    extra = 0
    fields = ("relation_type", "color", "size")


# ---------------------------------------------------------
# CypherQuery Admin
# ---------------------------------------------------------

@admin.register(CypherQuery)
class CypherQueryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    search_fields = ("name", "description", "query")
    ordering = ("name",)

    inlines = [NodeStyleInline, EdgeStyleInline]
    actions = ["create_missing_styles"]

    @admin.action(description="Create missing styles")
    def create_missing_styles(modeladmin, request, queryset):
        factory = StyleFactory()
        total_created = 0

        for cypher_query in queryset:
            total_created += factory.create_missing_styles(cypher_query)

        messages.success(request, f"Created {total_created} missing styles.")


# ---------------------------------------------------------
# GraphWorkflow Admin
# ---------------------------------------------------------

@admin.register(GraphWorkflow)
class GraphWorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "cypher_query", "lambda_node", "is_active")
    list_filter = ("is_active", "cypher_query")
    search_fields = ("name", "slug", "description")
    ordering = ("name",)


# ---------------------------------------------------------
# DynamicPage Admin
# ---------------------------------------------------------

@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "open_link")
    list_filter = ("is_active", )
    search_fields = ("name", "slug")
    ordering = ("name",)

    def open_link(self, obj):
        url = reverse("webfront:dynamic_page", args=[obj.slug])
        return format_html('<a href="{}" target="_blank">Open</a>', url)

    open_link.short_description = "View"

    # def save_model(self, request, obj, form, change):
    #     if not obj.owner:
    #         obj.owner = request.user
    #     super().save_model(request, obj, form, change)


# ---------------------------------------------------------
# FileWorkflow Admin
# ---------------------------------------------------------

@admin.register(FileWorkflow)
class FileWorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "bucket", "is_active", "open_link")
    list_filter = ("bucket", "is_active")
    search_fields = ("name", "description")
    ordering = ("name",)

    def open_link(self, obj):
        url = reverse("webfront:workflow_upload", args=[obj.slug])
        return format_html('<a href="{}" target="_blank">Open</a>', url)

    open_link.short_description = "View"
