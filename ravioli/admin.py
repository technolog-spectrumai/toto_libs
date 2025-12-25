from django.contrib import admin, messages
from django.utils.html import format_html
from neo4j import GraphDatabase
from django.conf import settings
from django import forms
from django_json_widget.widgets import JSONEditorWidget
from toto.colors import ColorGenerator


from .graph import GraphTranslator
from toto.batch import BatchAction
from .models import (
    CypherQuery,
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
    NodeStyle,
    EdgeStyle,
)
from toto.admin import BaseSerializableAdmin
from .forms import DynamicDataNodeForm   # <-- import your dynamic form

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


# ---------------------------
# Custom Forms
# ---------------------------

class CollectionTypeForm(forms.ModelForm):
    class Meta:
        model = CollectionType
        fields = "__all__"
        widgets = {
            "json_schema": JSONEditorWidget(),   # JSON editor
            "form_layout": JSONEditorWidget(),
        }


class RelationTypeForm(forms.ModelForm):
    class Meta:
        model = RelationType
        fields = "__all__"
        widgets = {
            "json_schema": JSONEditorWidget(),
            "form_layout": JSONEditorWidget(),
        }


class DataEdgeForm(forms.ModelForm):
    class Meta:
        model = DataEdge
        fields = "__all__"
        widgets = {
            "metadata": JSONEditorWidget(),
        }


class NodeStyleInline(admin.TabularInline):
    model = NodeStyle
    extra = 0
    fields = ("collection_type", "color", "size")


class EdgeStyleInline(admin.TabularInline):
    model = EdgeStyle
    extra = 0
    fields = ("relation_type", "color", "size")


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


class GraphForm(forms.ModelForm):
    class Meta:
        model = Graph
        fields = "__all__"
        widgets = {
            "conversion_rules": JSONEditorWidget(),  # if you named the field conversion_rules
            "config": JSONEditorWidget(),            # if you kept the name config
        }


@admin.register(Graph)
class GraphAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_by", "created_at")
    search_fields = ("name", "description")
    list_filter = ("created_by",)

    actions = ["export_graphs_to_neo4j"]

    @admin.action(description="Export selected graphs to Neo4j")
    def export_graphs_to_neo4j(self, request, queryset):
        def export_one(graph_obj):
            translator = GraphTranslator(graph_obj)
            translator.export()
            return graph_obj

        result = BatchAction(queryset).run(export_one)
        BatchAction.display_messages(result, self.message_user, request, verb="export")

@admin.register(CollectionType)
class CollectionTypeAdmin(admin.ModelAdmin):
    form = CollectionTypeForm
    list_display = ("name", "created_at")
    search_fields = ("name",)


@admin.register(DataNode)
class DataNodeAdmin(admin.ModelAdmin):
    list_display = ("name", "collection_type", "graph", "created_at")
    search_fields = ("name", "data")
    list_filter = ("collection_type", "graph")
    exclude = ("data",)  # hide raw JSON textarea

    def get_form(self, request, obj=None, **kwargs):
        """
        Return a form class that injects layout_json from the object's collection_type.
        """
        layout = None
        if obj and obj.collection_type and obj.collection_type.form_layout:
            layout = obj.collection_type.form_layout

        class ParametricDynamicForm(DynamicDataNodeForm):
            def __init__(self, *args, **kw):
                kw["layout_json"] = layout
                super().__init__(*args, **kw)

        return ParametricDynamicForm

    def get_fieldsets(self, request, obj=None):
        """
        Split into two fieldsets:
        - Basic info: name + collection_type
        - Data: dynamic fields
        """
        form_class = self.get_form(request, obj)
        form = form_class(instance=obj)

        all_fields = list(form.fields.keys())
        basic_fields = ["name", "collection_type", "graph"]
        dynamic_fields = [f for f in all_fields if f not in basic_fields]

        return [
            ("Basic info", {"fields": basic_fields}),
            ("Data", {"fields": dynamic_fields}),
        ]


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



