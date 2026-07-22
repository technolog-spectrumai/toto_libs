"""CORS + data_mesh-gated JSON API for the knowledge graph, consumed by Aurora.

Two read endpoints:
  - list the saved "Knowledge Graph Queries" (``CypherQuery``) a user may run —
    exactly the admin-defined set the ravioli query page shows;
  - run one of them synchronously, returning a Cytoscape-shaped ``{nodes, edges}``.

Gating (data_mesh), CORS and Bearer auth all come from :class:`MeshGatedApiView`;
the graph execution reuses the same ``Neo4jClient`` flow as the server-rendered
ravioli query view (``query_graph_data``). Saved queries are admin-curated, so we
run them as-is — there is no free-text Cypher surface here.

Neo4j may be absent (driver not installed), disabled, or down on the server we
connect to. The endpoints fail gracefully: the list is SQL-only and always works;
the run endpoint returns ``503 {"graph_unavailable": true}`` rather than erroring.
"""
from django.http import JsonResponse

from toto.api.cors import MeshGatedApiView

from .models import CypherQuery


def _graph_unavailable(detail):
    return JsonResponse(
        {"error": detail, "graph_unavailable": True}, status=503
    )


class KgQueryListApiView(MeshGatedApiView):
    """GET → the saved knowledge-graph queries the user can run.

    SQL-only (no Neo4j), so it works even when the graph database is down — the
    user can still browse the query list and gets a graceful message on run.
    """

    def get(self, request):
        queries = CypherQuery.objects.all().order_by("name")
        return JsonResponse({
            "queries": [
                {"id": q.id, "name": q.name, "description": q.description}
                for q in queries
            ]
        })


class KgQueryRunApiView(MeshGatedApiView):
    """GET → run a saved query and return its graph as ``{nodes, edges}``."""

    def get(self, request, query_id):
        try:
            query = CypherQuery.objects.get(id=query_id)
        except CypherQuery.DoesNotExist:
            return JsonResponse({"error": "Query not found."}, status=404)

        # Import lazily and defensively: the neo4j driver may not be installed,
        # the ravioli graph may be disabled, or Neo4j may be down — none of which
        # should 500. Each path returns a graceful 503 the client can render.
        try:
            from .connection import Neo4jClient, is_enabled
        except Exception:
            return _graph_unavailable("Graph database is not available on this server.")

        if not is_enabled():
            return _graph_unavailable("Graph functionality is not enabled on this server.")

        client = None
        try:
            client = Neo4jClient()
            records = client.run_cypher(query.query)
            nodes, edges = client.extract_graph(records)
        except Exception as exc:  # connection refused / driver missing / Neo4j down
            return _graph_unavailable(f"Graph database is unavailable: {exc}")
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass

        return JsonResponse({
            "nodes": nodes,
            "edges": edges,
            "selected_query": {
                "id": query.id,
                "name": query.name,
                "description": query.description,
            },
        })
