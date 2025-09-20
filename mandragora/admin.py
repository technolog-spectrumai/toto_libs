from django.contrib import admin, messages
from .models import (
    Workflow, Node, FunctionNode, AlgoNode, Edge,
    WorkflowRun, NodeRun
)
from .executor import WorkflowExecutor
from django.utils.safestring import mark_safe


def render_mermaid_diagram(diagram: str) -> str:
    html = f"""
    <div class="mermaid">
    {diagram}
    </div>
    <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({{ startOnLoad: true }});</script>
    """
    return mark_safe(html)



@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("name", "description", "created_at")
    search_fields = ("name", "description")
    readonly_fields = ("created_at", "mermaid_dag")
    actions = ["start_workflow_action"]

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
        return render_mermaid_diagram(diagram)

    mermaid_dag.short_description = "Workflow DAG"

    def start_workflow_action(self, request, queryset):
        started = 0
        for workflow in queryset:
            WorkflowExecutor.start_workflow(workflow.id)
            started += 1
        self.message_user(request, f"Started {started} workflow(s).", messages.SUCCESS)

    start_workflow_action.short_description = "Start selected workflow(s)"


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
    readonly_fields = ("started_at", "finished_at", "dag_status")

    fieldsets = (
        (None, {
            "fields": ("workflow", "status", "started_at", "finished_at", "dag_status")
        }),
    )

    def dag_status(self, obj):
        node_runs = NodeRun.objects.filter(workflow_run=obj).select_related("node")
        edges = obj.workflow.edges.select_related("source", "target")

        run_map = {nr.node_id: nr for nr in node_runs}
        lines = ["flowchart TD"]
        style_lines = []

        for node in obj.workflow.nodes.all():
            key = node.name.replace(" ", "_")
            label = node.name
            lines.append(f'{key}["{label}"]')

            if node.id in run_map:
                status = run_map[node.id].status
                if status == "success":
                    style_lines.append(f'style {key} fill:#d1fae5,stroke:#065f46,stroke-width:2px')
                elif status == "failed":
                    style_lines.append(f'style {key} fill:#fee2e2,stroke:#991b1b,stroke-width:2px')
                elif status == "pending":
                    style_lines.append(f'style {key} fill:#fef9c3,stroke:#92400e,stroke-width:2px')
            else:
                style_lines.append(f'style {key} fill:#e5e7eb,stroke:#374151,stroke-width:2px')

        for edge in edges:
            src = edge.source.name.replace(" ", "_")
            tgt = edge.target.name.replace(" ", "_")
            lines.append(f"{src} --> {tgt}")

        lines.extend(style_lines)
        diagram = "\n".join(lines)
        return render_mermaid_diagram(diagram)

    dag_status.short_description = "DAG Execution Status"


@admin.register(NodeRun)
class NodeRunAdmin(admin.ModelAdmin):
    list_display = ("node", "workflow_run", "status", "started_at", "finished_at", "terminated")
    list_filter = ("status", "node__workflow")
    search_fields = ("node__name", "workflow_run__workflow__name")
    readonly_fields = ("started_at", "finished_at", "input_data", "output_data", "error")
    actions = ["terminate_node_runs"]

    def terminate_node_runs(self, request, queryset):
        count = 0
        for node_run in queryset:
            if node_run.status not in ["success", "failed", "terminated"]:
                WorkflowExecutor.revoke_node_task(node_run.id, reason="Terminated via admin")
                count += 1
        self.message_user(request, f"{count} node run(s) terminated.", level=messages.WARNING)

    terminate_node_runs.short_description = "Force terminate selected node runs"