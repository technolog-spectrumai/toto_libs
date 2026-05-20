import json

from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path

from .models import CypherQuery, CypherQueryResult, GraphChangeEvent, GraphSync


@admin.register(CypherQuery)
class CypherQueryAdmin(admin.ModelAdmin):
    list_display = ("name",)


@admin.register(CypherQueryResult)
class CypherQueryResultAdmin(admin.ModelAdmin):
    list_display = ("query",)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        from .connection import Neo4jClient, is_enabled

        obj = CypherQueryResult.objects.get(pk=object_id)
        context = {
            "title": f"Graph Viewer: {obj.query.name}",
            "query": obj.query.query,
            "nodes": [],
            "edges": [],
        }

        if is_enabled():
            client = Neo4jClient()
            try:
                records = client.run_cypher(obj.query.query)
                nodes, edges = client.extract_graph(records)
            finally:
                client.close()

            for n in nodes:
                n["props"] = json.dumps(n["props"])
            for e in edges:
                e["props"] = json.dumps(e["props"])

            context["nodes"] = nodes
            context["edges"] = edges

        return render(request, "admin/cypher_results.html", context)


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
        from .connection import Neo4jClient, is_enabled
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
