import json

from django.contrib import admin
from django.shortcuts import render

from .models import CypherQuery, CypherQueryResult


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
