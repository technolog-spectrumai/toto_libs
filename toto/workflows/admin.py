from django.contrib import admin
from django.utils.html import format_html

from .models import (
    LambdaFunction, WorkflowConnector, Workflow, WorkflowNode, WorkflowEdge,
    WorkflowRun, WorkflowNodeRun, WorkflowEdgeRun, HumanTask,
)

_RUN_STATUS_COLORS = {
    "pending":   "#6b7280",
    "running":   "#2563eb",
    "paused":    "#d97706",
    "completed": "#16a34a",
    "failed":    "#dc2626",
    "waiting":   "#d97706",
    "skipped":   "#9ca3af",
}


def _run_badge(status_val, label):
    color = _RUN_STATUS_COLORS.get(status_val, "#6b7280")
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 8px;border-radius:9999px;'
        'font-size:11px;font-weight:600">{}</span>',
        color, label,
    )


class WorkflowNodeInline(admin.TabularInline):
    model = WorkflowNode
    extra = 0
    fields = ("id", "node_type", "label", "lambda_function", "connector")
    readonly_fields = ("id",)


class WorkflowEdgeInline(admin.TabularInline):
    model = WorkflowEdge
    extra = 0
    fields = ("source", "target", "branch_key", "is_default")
    fk_name = "workflow"


@admin.register(LambdaFunction)
class LambdaFunctionAdmin(admin.ModelAdmin):
    list_display = ("id", "function_name", "kernel")
    search_fields = ("function_name",)


@admin.register(WorkflowConnector)
class WorkflowConnectorAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "connector_type", "created_at")
    list_filter = ("connector_type",)
    search_fields = ("name",)
    readonly_fields = ("created_at",)


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "node_count", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    inlines = [WorkflowNodeInline, WorkflowEdgeInline]

    @admin.display(description="Nodes")
    def node_count(self, obj):
        return obj.nodes.count()


@admin.register(WorkflowNode)
class WorkflowNodeAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow", "node_type", "label", "lambda_function", "connector")
    list_filter = ("node_type", "workflow")
    search_fields = ("label",)


@admin.register(WorkflowEdge)
class WorkflowEdgeAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow", "source", "target", "branch_key", "is_default")
    list_filter = ("workflow",)


class WorkflowNodeRunInline(admin.TabularInline):
    model = WorkflowNodeRun
    extra = 0
    fields = ("node", "status_badge", "celery_task_id", "started_at", "completed_at")
    readonly_fields = ("status_badge", "celery_task_id", "started_at", "completed_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _run_badge(obj.status, obj.get_status_display())


class WorkflowEdgeRunInline(admin.TabularInline):
    model = WorkflowEdgeRun
    extra = 0
    fields = ("edge", "activated", "activated_at")
    readonly_fields = ("activated_at",)


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow", "status_badge", "started_at", "completed_at")
    list_filter = ("status", "workflow")
    readonly_fields = ("created_at", "started_at", "completed_at")
    inlines = [WorkflowNodeRunInline, WorkflowEdgeRunInline]

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _run_badge(obj.status, obj.get_status_display())


@admin.register(WorkflowNodeRun)
class WorkflowNodeRunAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow_run", "node", "status_badge", "celery_task_id", "started_at")
    list_filter = ("status",)
    readonly_fields = ("celery_task_id", "started_at", "completed_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _run_badge(obj.status, obj.get_status_display())


@admin.register(HumanTask)
class HumanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "node_run", "status", "submitted_at", "created_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "submitted_at")
