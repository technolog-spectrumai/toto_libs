from django.contrib import admin
from django import forms

from django_ace import AceWidget
from django_json_widget.widgets import JSONEditorWidget

from .models import (
    CypherQuery,
    NodeStyle,
    EdgeStyle,
    Widget,
    DynamicPage
)

from ravioli.models import CollectionType, RelationType
from toto.colors import ColorGenerator


# ---------------------------------------------------------
# Forms
# ---------------------------------------------------------

class CypherQueryForm(forms.ModelForm):
    class Meta:
        model = CypherQuery
        fields = "__all__"
        widgets = {
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="400px"),
            "test_context": JSONEditorWidget(),
        }


class WidgetForm(forms.ModelForm):
    class Meta:
        model = Widget
        fields = "__all__"
        widgets = {
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="300px"),
            "config": JSONEditorWidget(),
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


# ---------------------------------------------------------
# Widget Admin
# ---------------------------------------------------------

@admin.register(Widget)
class WidgetAdmin(admin.ModelAdmin):
    form = WidgetForm
    list_display = ("name", "type")
    list_filter = ("type",)
    search_fields = ("name",)
    ordering = ("name",)


# ---------------------------------------------------------
# DynamicPage Admin
# ---------------------------------------------------------

@admin.register(DynamicPage)
class DynamicPageAdmin(admin.ModelAdmin):
    form = DynamicPageForm
    list_display = ("name", "created_by", "created_at")
    list_filter = ("created_by",)
    search_fields = ("name", "query")
    readonly_fields = ("created_at",)
    ordering = ("name",)
    filter_horizontal = ("widgets",)
