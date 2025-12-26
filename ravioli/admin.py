from django.contrib import admin, messages
from django import forms
from django_json_widget.widgets import JSONEditorWidget
from toto.colors import ColorGenerator
from .translate import GraphTranslator
from toto.batch import BatchAction
from .models import (
    Graph,
    CollectionType,
    DataNode,
    RelationType,
    DataEdge,
    Collector
)
from .forms import DynamicDataNodeForm
from django_json_widget.widgets import JSONEditorWidget
from django.conf import settings

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



class CollectorAdminForm(forms.ModelForm):
    class Meta:
        model = Collector
        fields = "__all__"
        widgets = {
            "node_map": JSONEditorWidget,
            "edge_map": JSONEditorWidget,
        }


@admin.register(Collector)
class CollectorAdmin(admin.ModelAdmin):
    form = CollectorAdminForm

    list_display = ("app_name", "created_at")
    search_fields = ("app_name",)
    list_filter = ("app_name", "created_at")

