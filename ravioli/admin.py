from django.contrib import admin, messages
from django.utils.html import format_html
from neo4j import GraphDatabase
from django.conf import settings
from django_ace import AceWidget   # <-- ACE editor widget
from django import forms

from .models import (
    CypherQuery,
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
)
from toto.admin import BaseSerializableAdmin


# Use connection details from settings.py
driver = GraphDatabase.driver(
    f"bolt://{settings.NEO4J_HOST}:{settings.NEO4J_PORT}",
    auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD)
)


def test_cypher_query(query: str) -> bool:
    try:
        with driver.session() as session:
            session.run(query).consume()
        return True
    except Exception:
        return False


# ---------------------------
# Custom Forms with ACE editor
# ---------------------------

class CollectionTypeForm(forms.ModelForm):
    class Meta:
        model = CollectionType
        fields = "__all__"
        widgets = {
            "json_schema": AceWidget(mode="json", theme="chrome"),
            "form_layout": AceWidget(mode="json", theme="chrome"),
        }


class DataNodeForm(forms.ModelForm):
    class Meta:
        model = DataNode
        fields = "__all__"
        widgets = {
            "data": AceWidget(mode="json", theme="chrome"),
        }


class RelationTypeForm(forms.ModelForm):
    class Meta:
        model = RelationType
        fields = "__all__"
        widgets = {
            "json_schema": AceWidget(mode="json", theme="chrome"),
            "form_layout": AceWidget(mode="json", theme="chrome"),
        }


class DataEdgeForm(forms.ModelForm):
    class Meta:
        model = DataEdge
        fields = "__all__"
        widgets = {
            "metadata": AceWidget(mode="json", theme="chrome"),
        }


# ---------------------------
# Admin registrations
# ---------------------------

@admin.register(CypherQuery)
class CypherQueryAdmin(BaseSerializableAdmin):
    list_display = ("name", "created_by", "created_at", "is_active", "query_valid")
    list_filter = ("is_active", "created_by")
    search_fields = ("name", "description", "query")
    ordering = ("name",)
    readonly_fields = ("created_at",)

    def query_valid(self, obj):
        if test_cypher_query(obj.query):
            return format_html('<span style="color: green; font-weight: bold;">Yes</span>')
        return format_html('<span style="color: red; font-weight: bold;">No</span>')
    query_valid.short_description = "Valid?"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if test_cypher_query(obj.query):
            self.message_user(request, f"Cypher query '{obj.name}' validated successfully.", level=messages.SUCCESS)
        else:
            self.message_user(request, f"Cypher query '{obj.name}' failed validation.", level=messages.ERROR)


@admin.register(Graph)
class GraphAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_by", "created_at")
    search_fields = ("name", "description")
    list_filter = ("created_by",)


@admin.register(CollectionType)
class CollectionTypeAdmin(admin.ModelAdmin):
    form = CollectionTypeForm
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(DataNode)
class DataNodeAdmin(admin.ModelAdmin):
    form = DataNodeForm
    list_display = ("name", "collection_type", "graph", "created_at")
    search_fields = ("name", "data")
    list_filter = ("collection_type", "graph")


@admin.register(RelationType)
class RelationTypeAdmin(admin.ModelAdmin):
    form = RelationTypeForm
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(DataEdge)
class DataEdgeAdmin(admin.ModelAdmin):
    form = DataEdgeForm
    list_display = ("source", "target", "relation_type", "graph", "label", "created_at")
    search_fields = ("label", "metadata")
    list_filter = ("relation_type", "graph")
