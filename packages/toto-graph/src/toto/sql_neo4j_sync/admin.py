from django.contrib import admin, messages
from django.shortcuts import redirect, render
from django.urls import path

from .models import (
    GraphChangeEvent,
    GraphProjectionPlan,
    GraphSync,
    GraphSyncSchedule,
)


@admin.register(GraphChangeEvent)
class GraphChangeEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "action",
        "status",
        "graph_label",
        "object_uuid",
        "attempts",
        "created_at",
        "processed_at",
    )
    list_filter = ("status", "action", "graph_label")
    search_fields = ("graph_label", "object_uuid", "model_path", "error")
    readonly_fields = (
        "action",
        "graph_label",
        "model_path",
        "object_uuid",
        "payload",
        "status",
        "attempts",
        "error",
        "created_at",
        "updated_at",
        "processed_at",
    )


@admin.register(GraphProjectionPlan)
class GraphProjectionPlanAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "total_changes", "created_at", "applied_at")
    list_filter = ("status",)
    readonly_fields = (
        "status",
        "scope",
        "summary",
        "diff",
        "error",
        "created_at",
        "updated_at",
        "applied_at",
    )


@admin.register(GraphSync)
class GraphSyncAdmin(admin.ModelAdmin):
    change_list_template = "admin/graph_sync.html"

    def get_queryset(self, request):
        return GraphSync.objects.none()

    def changelist_view(self, request, extra_context=None):
        from .loader import grouped_models, load_all_configs

        extra_context = extra_context or {}
        extra_context["grouped_models"] = grouped_models(load_all_configs())
        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "run-sync/",
                self.admin_site.admin_view(self.run_sync),
                name="graph-sync",
            ),
        ]
        return custom + urls

    def run_sync(self, request):
        from toto.ravioli.connection import Neo4jClient, is_enabled

        from .loader import load_all_configs
        from .projection import ProjectionRunner

        if not is_enabled():
            messages.error(request, "ravioli is not enabled (RAVIOLI_ENABLED=False).")
            return redirect("..")

        selected = request.POST.getlist("models")
        configs = load_all_configs()
        client = Neo4jClient()
        try:
            runner = ProjectionRunner(client, configs)
            if selected:
                for _ in runner.run_with_progress(selected_labels=selected):
                    pass
            else:
                runner.run()
        finally:
            client.close()

        messages.success(
            request,
            f"Graph sync completed for: {', '.join(selected) if selected else 'all'}",
        )
        return redirect("..")


@admin.register(GraphSyncSchedule)
class GraphSyncScheduleAdmin(admin.ModelAdmin):
    list_display = (
        "enabled",
        "interval_minutes",
        "last_run_at",
        "last_run_status",
    )
    readonly_fields = ("last_run_at", "last_run_status", "last_error")
    fields = (
        "enabled",
        "interval_minutes",
        "last_run_at",
        "last_run_status",
        "last_error",
    )

    def has_add_permission(self, request):
        return not GraphSyncSchedule.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def get_queryset(self, request):
        GraphSyncSchedule.get_config()
        return super().get_queryset(request)
