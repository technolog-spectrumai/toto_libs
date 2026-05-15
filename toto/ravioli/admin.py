from django.contrib import admin, messages
from django.shortcuts import render, redirect
from django.urls import path
from .models import CypherQuery, CypherQueryResult, GraphSync
from .neo4j_client import Neo4jClient
import json
from .projection import ProjectionRunner, ProjectionRegistry


# ---------------------------------------------------------
# NORMAL ADMIN FOR CYPHER QUERY
# ---------------------------------------------------------
@admin.register(CypherQuery)
class CypherQueryAdmin(admin.ModelAdmin):
    list_display = ("name",)
    # No fancy template — normal Django admin


# ---------------------------------------------------------
# FANCY GRAPH VIEWER FOR QUERY RESULTS
# ---------------------------------------------------------

@admin.register(CypherQueryResult)
class CypherQueryResultAdmin(admin.ModelAdmin):
    list_display = ("query",)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        obj = CypherQueryResult.objects.get(pk=object_id)

        client = Neo4jClient()
        records = client.run_cypher(obj.query.query)
        nodes, edges = client.extract_graph(records)

        for n in nodes:
            n["props"] = json.dumps(n["props"])

        for e in edges:
            e["props"] = json.dumps(e["props"])

        context = {
            "title": f"Graph Viewer: {obj.query.name}",
            "query": obj.query.query,
            "nodes": nodes,
            "edges": edges,
        }

        return render(request, "admin/cypher_results.html", context)


@admin.register(GraphSync)
class GraphSyncAdmin(admin.ModelAdmin):
    change_list_template = "admin/graph_sync.html"

    def get_queryset(self, request):
        return GraphSync.objects.none()

    def changelist_view(self, request, extra_context=None):
        registry = ProjectionRegistry()

        extra_context = extra_context or {}
        extra_context["grouped_models"] = registry.grouped_models()

        return super().changelist_view(request, extra_context=extra_context)

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("run-sync/", self.admin_site.admin_view(self.run_sync), name="graph-sync"),
        ]
        return custom + urls

    def run_sync(self, request):
        selected = request.POST.getlist("models")

        runner = ProjectionRunner(selected_models=selected)
        runner.run()

        messages.success(request, f"Graph sync completed for: {', '.join(selected)}")
        return redirect("..")






