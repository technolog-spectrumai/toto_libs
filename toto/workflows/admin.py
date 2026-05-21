from django.contrib import admin
from django.db import models
from django.utils.html import format_html
from jsoneditor.forms import JSONEditor

from .models import (
    HumanTask,
    LambdaFunction,
    Report,
    ReportPage,
    ReportTemplate,
    WorkflowConnector,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRunFile,
    WorkflowRun,
    WorkflowTriggerInput,
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

JSON_EDITOR_WIDGET = JSONEditor(
    init_options={
        "mode": "code",
        "modes": ["code", "tree", "form", "view"],
        "search": True,
        "history": True,
    }
)


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
    show_change_link = True
    fields = ("id", "node_type", "label", "lambda_function", "connector", "report_template")
    readonly_fields = ("id",)


class WorkflowTriggerInputInline(admin.TabularInline):
    model = WorkflowTriggerInput
    extra = 0
    fields = (
        "order",
        "key",
        "label",
        "input_type",
        "required",
        "default_value",
        "help_text",
        "allow_multiple_files",
        "accepted_file_types",
        "max_file_count",
        "max_file_size",
    )
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


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
    fields = ("name", "connector_type", "config", "created_at")
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


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
    list_display = ("id", "workflow", "node_type", "label", "lambda_function", "connector", "report_template")
    list_filter = ("node_type", "workflow")
    search_fields = ("label",)
    inlines = [WorkflowTriggerInputInline]
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }

    def get_inline_instances(self, request, obj=None):
        if obj is None or obj.node_type != WorkflowNode.TRIGGER:
            return []
        return super().get_inline_instances(request, obj)


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


class WorkflowRunFileInline(admin.TabularInline):
    model = WorkflowRunFile
    extra = 0
    fields = ("input_key", "original_name", "mime_type", "size", "uploaded_at")
    readonly_fields = ("input_key", "original_name", "mime_type", "size", "uploaded_at")


@admin.register(WorkflowRun)
class WorkflowRunAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow", "status_badge", "started_at", "completed_at")
    list_filter = ("status", "workflow")
    readonly_fields = ("created_at", "started_at", "completed_at")
    inlines = [WorkflowNodeRunInline, WorkflowEdgeRunInline, WorkflowRunFileInline]

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _run_badge(obj.status, obj.get_status_display())


class ReportPageInline(admin.TabularInline):
    model = ReportPage
    extra = 0
    fields = ("key", "title", "order")


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "report_type", "updated_at")
    list_filter = ("report_type",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    fields = ("name", "slug", "report_type", "description", "definition", "created_at", "updated_at")
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "report_type", "template", "workflow_run", "status", "created_at")
    list_filter = ("report_type", "status", "template")
    search_fields = ("title", "slug")
    prepopulated_fields = {"slug": ("title",)}
    readonly_fields = ("created_at", "updated_at")
    inlines = [ReportPageInline]
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


@admin.register(ReportPage)
class ReportPageAdmin(admin.ModelAdmin):
    list_display = ("id", "report", "key", "title", "order")
    list_filter = ("report",)
    search_fields = ("title", "key", "report__title")
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


@admin.register(WorkflowNodeRun)
class WorkflowNodeRunAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow_run", "node", "status_badge", "celery_task_id", "started_at")
    list_filter = ("status",)
    readonly_fields = ("celery_task_id", "started_at", "completed_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _run_badge(obj.status, obj.get_status_display())


@admin.register(WorkflowTriggerInput)
class WorkflowTriggerInputAdmin(admin.ModelAdmin):
    list_display = ("id", "trigger_node", "key", "input_type", "required", "order")
    list_filter = ("input_type", "required", "trigger_node__workflow")
    search_fields = ("key", "label", "trigger_node__label", "trigger_node__workflow__name")
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


@admin.register(WorkflowRunFile)
class WorkflowRunFileAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow_run", "input_key", "original_name", "mime_type", "size", "uploaded_at")
    list_filter = ("input_key", "mime_type")
    search_fields = ("original_name", "input_key", "workflow_run__workflow__name")
    readonly_fields = ("uploaded_at",)


@admin.register(HumanTask)
class HumanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "node_run", "status", "submitted_at", "created_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "submitted_at")
