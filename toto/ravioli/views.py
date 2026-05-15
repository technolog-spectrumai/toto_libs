import json
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_GET

from .models import CypherQuery
from toto.core.page import PageProcessor
from .neo4j_client import Neo4jClient
from .projection import ProjectionRegistry, ProjectionRunner


def superuser_required(view_func):
    return user_passes_test(lambda user: user.is_active and user.is_superuser)(view_func)


def query_unified_view(request):
    queries = CypherQuery.objects.all().order_by("name")

    selected_id = request.GET.get("query")
    selected_query = None

    if selected_id:
        selected_query = get_object_or_404(CypherQuery, pk=selected_id)

    context = {
        "queries": queries,
        "selected_query": selected_query,
    }

    context = PageProcessor().decorate(context, request)

    return render(request, "ravioli/query_unified.html", context)


@superuser_required
def projection_sync_view(request):
    registry = ProjectionRegistry()
    context = {
        "grouped_models": registry.grouped_models(),
    }

    return render(
        request,
        "ravioli/projection_sync.html",
        PageProcessor().decorate(context, request),
    )


def query_graph_data(request, query_id):
    selected_query = CypherQuery.objects.get(id=query_id)
    queries = CypherQuery.objects.all().values("id", "name")

    client = Neo4jClient()
    records = client.run_cypher(selected_query.query)
    nodes, edges = client.extract_graph(records)
    client.close()

    return JsonResponse({
        "nodes": nodes,
        "edges": edges,
        "query": selected_query.query,
        "queries": list(queries),
        "selected_query": {
            "id": selected_query.id,
            "name": selected_query.name,
        }
    })


@require_GET
@superuser_required
def run_projection_stream(request):
    selected_models = request.GET.getlist("models") or None

    def event_stream():
        runner = ProjectionRunner(selected_models=selected_models)

        try:
            for event in runner.run_with_progress():
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield "data: " + json.dumps({
                "status": "error",
                "message": str(exc),
            }) + "\n\n"

    response = StreamingHttpResponse(
        event_stream(),
        content_type="text/event-stream",
    )
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"

    return response