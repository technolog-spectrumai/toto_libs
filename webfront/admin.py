from django.contrib import admin, messages
from django import forms
from django_ace import AceWidget
from django_json_widget.widgets import JSONEditorWidget
from .models import (
    CypherQuery,
    NodeStyle,
    EdgeStyle,
    DynamicPage
)

from ravioli.models import CollectionType, RelationType
from toto.colors import ColorGenerator
from django.utils.html import format_html
from django.urls import reverse


class CypherQueryForm(forms.ModelForm):
    class Meta:
        model = CypherQuery
        fields = "__all__"
        widgets = {
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="400px"),
            "test_context": JSONEditorWidget(),
        }



class DynamicPageForm(forms.ModelForm):
    class Meta:
        model = DynamicPage
        fields = "__all__"
        widgets = {
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="400px"),
            "test_context": JSONEditorWidget(),
        }


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
    form = CypherQueryForm
    list_display = ("name", "created_by", "created_at", "is_active")
    list_filter = ("is_active", "created_by")
    search_fields = ("name", "description", "query")
    readonly_fields = ("created_at",)
    ordering = ("name",)
    inlines = [NodeStyleInline, EdgeStyleInline]
    actions = ["create_missing_styles"]


    @admin.action(description="Create missing styles")
    def create_missing_styles(modeladmin, request, queryset):
        created_count = 0
        node_color_generator = ColorGenerator("tab20")
        edge_color_generator = ColorGenerator("tab20c")

        for cypher_query in queryset:

            # Node styles — use enumerate index
            for idx, ct in enumerate(CollectionType.objects.all()):
                obj, created = NodeStyle.objects.get_or_create(
                    cypher_query=cypher_query,
                    collection_type=ct,
                    defaults={
                        "color": node_color_generator.color_for_id(idx),
                        "size": 20,
                    }
                )
                if created:
                    created_count += 1

            # Edge styles — also use enumerate index
            for idx, rt in enumerate(RelationType.objects.all()):
                obj, created = EdgeStyle.objects.get_or_create(
                    cypher_query=cypher_query,
                    relation_type=rt,
                    defaults={
                        "color": edge_color_generator.color_for_id(idx),
                        "size": 2,
                    }
                )
                if created:
                    created_count += 1

        messages.success(request, f"Created {created_count} missing styles.")



@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    form = DynamicPageForm  # optional, remove if not needed

    list_display = ("name", "slug", "owner", "open_link")
    list_filter = ("owner",)
    search_fields = ("name", "slug", "query")
    ordering = ("name",)

    def open_link(self, obj):
        """Clickable link to view the dynamic page."""
        url = reverse("webfront:dynamic_page", args=[obj.slug])
        return format_html('<a href="{}" target="_blank">Open</a>', url)

    open_link.short_description = "View"

    def save_model(self, request, obj, form, change):
        """Automatically assign owner if not set."""
        if not obj.owner:
            obj.owner = request.user
        super().save_model(request, obj, form, change)


