import subprocess

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    Cell, ComputeKernel, KernelDependency, Notebook,
)
from .views import ensure_lambda_kernel


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


class KernelDependencyInline(admin.TabularInline):
    model = KernelDependency
    extra = 1
    fields = ("package_name", "version_spec", "status_badge", "installed_at")
    readonly_fields = ("status_badge", "installed_at")

    @admin.display(description="Status")
    def status_badge(self, obj):
        return _badge(obj.install_status, obj.get_install_status_display())


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


class CellInline(admin.TabularInline):
    model = Cell
    extra = 0
    fields = ("position", "cell_type", "content", "execution_count", "stdout", "stderr", "rich_output")
    readonly_fields = ("execution_count", "stdout", "stderr", "rich_output")
    ordering = ("position",)


@admin.register(ComputeKernel)
class ComputeKernelAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "timeout_ms", "auto_close", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)
    inlines = [KernelDependencyInline]

    fieldsets = (
        ("Kernel Info", {"fields": ("name", "created_at")}),
        ("Execution Settings", {"fields": ("timeout_ms", "env", "auto_close")}),
    )


@admin.register(Notebook)
class NotebookAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "kernel")
    search_fields = ("title",)
    inlines = [CellInline]


@admin.register(Cell)
class CellAdmin(admin.ModelAdmin):
    list_display = ("id", "notebook", "cell_type", "position", "execution_count")
    list_filter = ("cell_type", "notebook")
    ordering = ("notebook", "position")
    search_fields = ("content",)
    actions = ["promote_to_lambda"]

    @admin.action(description="Promote selected cells to Lambda functions")
    def promote_to_lambda(self, request, queryset):
        from toto.workflows.models import LambdaFunction  # noqa: PLC0415
        created_count = updated_count = 0
        for cell in queryset.filter(cell_type="code"):
            function_name = f"notebook_cell_{cell.id}"
            lambda_fn, created = LambdaFunction.objects.update_or_create(
                function_name=function_name,
                defaults={"content": cell.content, "stdout": "", "stderr": ""},
            )
            ensure_lambda_kernel(lambda_fn)
            if created:
                created_count += 1
            else:
                updated_count += 1
        self.message_user(
            request,
            f"Promoted {created_count} new + {updated_count} updated lambda function(s).",
        )
