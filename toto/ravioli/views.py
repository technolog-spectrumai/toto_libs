import json

from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.http import require_GET

from .models import CypherQuery
from toto.core.page import PageProcessor


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def query_unified_view(request):
    queries = CypherQuery.objects.all().order_by("name")

    selected_id = request.GET.get("query")
    selected_query = None
    if selected_id:
        selected_query = get_object_or_404(CypherQuery, pk=selected_id)

    context = PageProcessor().decorate(
        {"queries": queries, "selected_query": selected_query},
        request,
    )
    return render(request, "ravioli/query_unified.html", context)


@superuser_required
def projection_sync_view(request):
    from .loader import grouped_models, load_all_configs

    context = PageProcessor().decorate(
        {"grouped_models": grouped_models(load_all_configs())},
        request,
    )
    return render(request, "ravioli/projection_sync.html", context)


def query_graph_data(request, query_id):
    from .connection import Neo4jClient, is_enabled

    selected_query = CypherQuery.objects.get(id=query_id)
    queries = CypherQuery.objects.all().values("id", "name")

    if not is_enabled():
        return JsonResponse(
            {"error": "Graph functionality is not enabled."},
            status=503,
        )

    client = Neo4jClient()
    try:
        records = client.run_cypher(selected_query.query)
        nodes, edges = client.extract_graph(records)
    finally:
        client.close()

    return JsonResponse({
        "nodes": nodes,
        "edges": edges,
        "query": selected_query.query,
        "queries": list(queries),
        "selected_query": {
            "id": selected_query.id,
            "name": selected_query.name,
        },
    })


@require_GET
@superuser_required
def run_projection_stream(request):
    from .connection import Neo4jClient, is_enabled
    from .loader import load_all_configs
    from .projection import ProjectionRunner

    selected_labels = request.GET.getlist("models") or None

    def event_stream():
        if not is_enabled():
            yield "data: " + json.dumps({
                "status": "error",
                "message": "RAVIOLI_ENABLED is False — cannot connect to Neo4j.",
            }) + "\n\n"
            return

        client = Neo4jClient()
        try:
            configs = load_all_configs()
            runner = ProjectionRunner(client, configs)
            for event in runner.run_with_progress(selected_labels=selected_labels):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield "data: " + json.dumps({
                "status": "error",
                "message": str(exc),
            }) + "\n\n"
        finally:
            client.close()

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
