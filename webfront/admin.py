from django.contrib import admin, messages
from django.utils.html import format_html
from neo4j import GraphDatabase
from django.conf import settings
from django import forms
from django_json_widget.widgets import JSONEditorWidget
from toto.colors import ColorGenerator
from django_ace import AceWidget
from .models import (
    CypherQuery,
    NodeStyle,
    EdgeStyle
)
from ravioli.models import CollectionType, RelationType

# ---------------------------
# Neo4j connection
# ---------------------------
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


class CypherQueryForm(forms.ModelForm):
    class Meta:
        model = CypherQuery
        fields = "__all__"
        widgets = {
            "code": AceWidget(mode="python", theme="chrome", width="100%", height="400px"),
            "test_context": JSONEditorWidget()
        }


class NodeStyleInline(admin.TabularInline):
    model = NodeStyle
    extra = 0
    fields = ("collection_type", "color", "size")


class EdgeStyleInline(admin.TabularInline):
    model = EdgeStyle
    extra = 0
    fields = ("relation_type", "color", "size")


@admin.register(CypherQuery)
class CypherQueryAdmin(admin.ModelAdmin):
    form = CypherQueryForm
    list_display = ("name", "created_by", "created_at", "is_active", "query_valid")
    list_filter = ("is_active", "created_by")
    search_fields = ("name", "description", "query")
    ordering = ("name",)
    readonly_fields = ("created_at",)
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