import subprocess

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Cell, ComputeKernel, KernelDependency, LambdaFunction, Notebook,
    Workflow, WorkflowNode, WorkflowEdge,
    WorkflowRun, WorkflowNodeRun, WorkflowEdgeRun, HumanTask,
)


# ---------------------------------------------------------
#  Status badge helper
# ---------------------------------------------------------

_STATUS_COLORS = {
    KernelDependency.PENDING: ("#6b7280", "gray"),
    KernelDependency.INSTALLING: ("#d97706", "yellow"),
    KernelDependency.INSTALLED: ("#16a34a", "green"),
    KernelDependency.FAILED: ("#dc2626", "red"),
}


def _badge(status, label):
    color, _ = _STATUS_COLORS.get(status, ("#6b7280", "gray"))
    return format_html(
        '<span style="background:{};color:#fff;padding:2px 8px;border-radius:9999px;font-size:11px;font-weight:600">{}</span>',
        color,
        label,
    )


# ---------------------------------------------------------
#  KernelDependency inline (used inside ComputeKernelAdmin)
# ---------------------------------------------------------

class KernelDependencyInline(admin.TabularInline):
    model = KernelDependency
    extra = 1
    fields = ("package_name", "version_spec", "status_badge", "installed_at")
    readonly_fields = ("status_badge", "installed_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _badge(obj.install_status, obj.get_install_status_display())


# ---------------------------------------------------------
#  KernelDependency standalone admin
# ---------------------------------------------------------

@admin.register(KernelDependency)
class KernelDependencyAdmin(admin.ModelAdmin):
    list_display = ("package_name", "version_spec", "kernel", "status_badge", "installed_at")
    list_filter = ("install_status", "kernel")
    search_fields = ("package_name",)
    readonly_fields = ("status_badge", "install_log", "installed_at")
    actions = ["pip_install"]

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _badge(obj.install_status, obj.get_install_status_display())

    @admin.action(description="Install selected packages via pip")
    def pip_install(self, request, queryset):
        ok = 0
        for dep in queryset:
            dep.install_status = KernelDependency.INSTALLING
            dep.install_log = ""
            dep.save(update_fields=["install_status", "install_log"])

            try:
                result = subprocess.run(
                    ["pip", "install", dep.pip_specifier()],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                dep.install_log = (result.stdout + result.stderr).strip()
                if result.returncode == 0:
                    dep.install_status = KernelDependency.INSTALLED
                    dep.installed_at = timezone.now()
                    ok += 1
                else:
                    dep.install_status = KernelDependency.FAILED
            except subprocess.TimeoutExpired:
                dep.install_log = "pip timed out after 120 s"
                dep.install_status = KernelDependency.FAILED

            dep.save(update_fields=["install_status", "install_log", "installed_at"])

        total = queryset.count()
        self.message_user(
            request,
            f"Installed {ok} of {total} package(s) successfully."
            if ok < total
            else f"All {total} package(s) installed successfully.",
        )


# ---------------------------------------------------------
#  ComputeKernel admin
# ---------------------------------------------------------

class CellInline(admin.TabularInline):
    model = Cell
    extra = 0
    fields = (
        "position",
        "cell_type",
        "content",
        "execution_count",
        "stdout",
        "stderr",
        "rich_output",
    )
    readonly_fields = (
        "execution_count",
        "stdout",
        "stderr",
        "rich_output",
    )
    ordering = ("position",)


@admin.register(ComputeKernel)
class ComputeKernelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "timeout_ms", "auto_close", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    inlines = [KernelDependencyInline]

    fieldsets = (
        ("Kernel Info", {
            "fields": ("name", "created_at")
        }),
        ("Execution Settings", {
            "fields": ("timeout_ms", "env", "auto_close")
        }),
    )


@admin.register(Notebook)
class NotebookAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "kernel")
    search_fields = ("title",)
    inlines = [CellInline]


@admin.register(Cell)
class CellAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "notebook",
        "cell_type",
        "position",
        "execution_count",
    )
    list_filter = ("cell_type", "notebook")
    ordering = ("notebook", "position")
    search_fields = ("content",)


@admin.register(LambdaFunction)
class LambdaFunctionAdmin(admin.ModelAdmin):
    list_display = ("id", "function_name", "kernel")
    search_fields = ("function_name",)


# ---------------------------------------------------------
#  Workflow admin
# ---------------------------------------------------------

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
    fields = ("id", "node_type", "label", "lambda_function")
    readonly_fields = ("id",)


class WorkflowEdgeInline(admin.TabularInline):
    model = WorkflowEdge
    extra = 0
    fields = ("source", "target", "branch_key", "is_default")
    fk_name = "workflow"


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
    list_display = ("id", "workflow", "node_type", "label", "lambda_function")
    list_filter = ("node_type", "workflow")
    search_fields = ("label",)


@admin.register(WorkflowEdge)
class WorkflowEdgeAdmin(admin.ModelAdmin):
    list_display = ("id", "workflow", "source", "target", "branch_key", "is_default")
    list_filter = ("workflow",)


class WorkflowNodeRunInline(admin.TabularInline):
    model = WorkflowNodeRun
    extra = 0
    fields = ("node", "status_badge", "started_at", "completed_at")
    readonly_fields = ("status_badge", "started_at", "completed_at")

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
    list_display = ("id", "workflow_run", "node", "status_badge", "started_at")
    list_filter = ("status",)
    readonly_fields = ("started_at", "completed_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _run_badge(obj.status, obj.get_status_display())


@admin.register(HumanTask)
class HumanTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "node_run", "status", "submitted_at", "created_at")
    list_filter = ("status",)
    readonly_fields = ("created_at", "submitted_at")
