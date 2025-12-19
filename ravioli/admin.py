from django.contrib import admin, messages
from django.utils.html import format_html
from neo4j import GraphDatabase
from django.conf import settings
from django import forms
from django_json_widget.widgets import JSONEditorWidget
from .graph import GraphTranslator
from toto.batch import BatchAction
from .models import (
    CypherQuery,
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
    DataTransform
)
from toto.admin import BaseSerializableAdmin
from .forms import DynamicDataNodeForm   # <-- import your dynamic form
from .conversion import GraphConversionPipeline

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


@admin.register(DataTransform)
class DataTransformAdmin(admin.ModelAdmin):
    list_display = ("name", "graph", "description")
    actions = ["run_transform"]

    @admin.action(description="Run selected transforms")
    def run_transform(self, request, queryset):

        def run_one(transform: DataTransform):
            app_labels = getattr(settings, "GRAPH_ALLOWED_APPS", [])
            if not app_labels:
                raise ValueError("GRAPH_ALLOWED_APPS is empty or missing in settings.")
            if not transform.graph:
                raise ValueError(f"Transform '{transform.name}' has no graph assigned.")
            pipeline = GraphConversionPipeline(
                app_labels=app_labels,
                transform_node=transform,
                graph=transform.graph
            )
            result = pipeline.run()
            if result is None:
                raise ValueError("Pipeline returned no result.")
            return transform

        result = BatchAction(queryset).run(run_one)

        BatchAction.display_messages(
            result,
            self.message_user,
            request,
            verb="run"
        )

