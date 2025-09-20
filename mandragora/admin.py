from django.contrib import admin
from .models import (
    Workflow, Node, FunctionNode, AlgoNode, Edge,
    WorkflowRun, NodeRun
)
from django.utils.safestring import mark_safe


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "mermaid_dag")

    fieldsets = (
        (None, {
            "fields": ("name", "description", "created_at", "mermaid_dag")
        }),
    )

    def mermaid_dag(self, obj):
        lines = ["graph TD"]
        for edge in obj.edges.select_related("source", "target").all():
            src = edge.source.name.replace(" ", "_")
            tgt = edge.target.name.replace(" ", "_")
            lines.append(f'{src}["{edge.source.name}"] --> {tgt}["{edge.target.name}"]')
        diagram = "\n".join(lines)
        html = f"""
        <div class="mermaid">
        {diagram}
        </div>
        <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        <script>mermaid.initialize({{ startOnLoad: true }});</script>
        """
        return mark_safe(html)

    mermaid_dag.short_description = "Workflow DAG"


@admin.register(Node)
class NodeAdmin(admin.ModelAdmin):
    list_display = ("name", "workflow", "created_at", "type", "countdown", "max_retries")
    list_filter = ("workflow",)
    search_fields = ("name",)
    readonly_fields = ("created_at",)

    def type(self, obj):
        return obj.get_subclass().__class__.__name__


@admin.register(FunctionNode)
class FunctionNodeAdmin(admin.ModelAdmin):
    list_display = ("name", "workflow", "created_at")
    search_fields = ("name", "code")
    readonly_fields = ("created_at",)
    fieldsets = (
        (None, {
            "fields": ("workflow", "name", "code", "countdown", "max_retries")
        }),
    )


@admin.register(AlgoNode)
class AlgoNodeAdmin(admin.ModelAdmin):
    list_display = ("name", "workflow", "algorithm", "created_at")
    list_filter = ("algorithm",)
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    fieldsets = (
        (None, {
            "fields": ("workflow", "name", "algorithm", "countdown", "max_retries")
        }),
    )


@admin.register(Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("workflow", "source", "target")
    list_filter = ("workflow",)
    search_fields = ("source__name", "target__name")


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ("workflow", "started_at", "finished_at", "status")
    list_filter = ("status", "workflow")
    search_fields = ("workflow__name",)
    readonly_fields = ("started_at", "finished_at")


@admin.register(NodeRun)
class NodeRunAdmin(admin.ModelAdmin):
    list_display = ("node", "workflow_run", "status", "started_at", "finished_at")
    list_filter = ("status", "node__workflow")
    search_fields = ("node__name", "workflow_run__workflow__name")
    readonly_fields = ("started_at", "finished_at", "input_data", "output_data", "error")
