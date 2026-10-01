import json

from django import forms
from django.contrib import admin
from django.db import models
from django.utils.html import format_html

from .models import (
    LambdaFunction,
    Report,
    ReportPage,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)

_RUN_STATUS_COLORS = {
    "pending":   "#6b7280",
    "running":   "#2563eb",
    "completed": "#16a34a",
    "failed":    "#dc2626",
    "skipped":   "#9ca3af",
}


class JsonTextarea(forms.Textarea):
    """The JSON fields' widget: a monospace box with the stored value indented
    (2026-10-01, 37c.27).

    It was django-jsoneditor's editor, and that package brought 1.7 MB of
    static (its own copy of ACE) into every image for these staff-only forms.
    Django's JSONField still refuses text that is not JSON and gives back what
    was typed; the indent only makes a stored value readable.
    """

    def __init__(self, attrs=None):
        super().__init__(attrs={"rows": 16, "class": "vLargeTextField",
                                "style": "font-family: monospace;", **(attrs or {})})

    def format_value(self, value):
        try:
            return json.dumps(json.loads(value), indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            return super().format_value(value)


JSON_EDITOR_WIDGET = JsonTextarea()


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
    fields = ("id", "node_type", "label", "lambda_function", "report_template")
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


@admin.register(Workflow)
class WorkflowAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "slug", "owner", "node_count", "created_at")
    search_fields = ("name", "slug", "owner__username")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at",)
    raw_id_fields = ("owner",)
    inlines = [WorkflowNodeInline, WorkflowEdgeInline]

    @admin.display(description="Nodes")
    def node_count(self, obj):
        return obj.nodes.count()


@admin.register(WorkflowNode)
class WorkflowNodeAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow", "node_type", "label", "lambda_function", "report_template")
    list_filter = ("node_type", "workflow")
    search_fields = ("label",)
    formfield_overrides = {
        models.JSONField: {"widget": JSON_EDITOR_WIDGET},
    }


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
    list_display = ("id", "workflow", "started_by", "status_badge",
                    "started_at", "completed_at")
    list_filter = ("status", "workflow")
    raw_id_fields = ("started_by",)
    readonly_fields = ("created_at", "started_at", "completed_at")
    inlines = [WorkflowNodeRunInline, WorkflowEdgeRunInline]

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


# Metering — the shared bases live in toto.quota.admin so every app's limits
# screen looks the same.
from toto.quota.admin import QuotaPolicyAdminBase, UsageEventAdminBase
from .models import WorkflowQuotaPolicy, WorkflowUsageEvent


@admin.register(WorkflowQuotaPolicy)
class WorkflowQuotaPolicyAdmin(QuotaPolicyAdminBase):
    pass


@admin.register(WorkflowUsageEvent)
class WorkflowUsageEventAdmin(UsageEventAdminBase):
    pass
