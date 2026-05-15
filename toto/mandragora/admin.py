from django.contrib import admin
from .models import Notebook, Cell, LambdaFunction, ComputeKernel


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
    list_display = ("id", "name", "timeout_ms", "created_at")
    search_fields = ("name",)
    readonly_fields = ("created_at",)

    fieldsets = (
        ("Kernel Info", {
            "fields": ("name", "created_at")
        }),
        ("Execution Settings", {
            "fields": ("timeout_ms", "env")
        }),
        ("Dependencies", {
            "fields": ("dependencies",)
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
