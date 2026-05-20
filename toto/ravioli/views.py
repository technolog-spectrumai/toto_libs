import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .models import CypherQuery, GraphProjectionPlan
from toto.ui import PageProcessor


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

    plans = GraphProjectionPlan.objects.all()[:10]
    context = PageProcessor().decorate(
        {
            "grouped_models": grouped_models(load_all_configs()),
            "plans": plans,
        },
        request,
    )
    return render(request, "ravioli/projection_sync.html", context)


@require_POST
@superuser_required
def create_projection_plan(request):
    from .connection import Neo4jClient, is_enabled
    from .loader import load_all_configs, validate_configs
    from .planner import create_projection_plan as build_projection_plan

    selected_labels = request.POST.getlist("models")
    if not selected_labels:
        messages.warning(request, "Select at least one graph label.")
        return redirect("ravioli:projection_sync")

    configs = load_all_configs()
    errors = validate_configs(configs)
    if errors:
        messages.error(request, "Graph config is invalid: " + "; ".join(errors))
        return redirect("ravioli:projection_sync")

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("ravioli:projection_sync")

    client = Neo4jClient()
    try:
        plan = build_projection_plan(
            client,
            labels=selected_labels,
            configs=configs,
        )
    except Exception as exc:
        messages.error(request, f"Could not create projection plan: {exc}")
        return redirect("ravioli:projection_sync")
    finally:
        client.close()

    messages.success(request, f"Projection plan #{plan.pk} created.")
    return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)


@superuser_required
def projection_plan_detail(request, plan_id):
    plan = get_object_or_404(GraphProjectionPlan, pk=plan_id)
    diff = plan.diff or {}
    node_diff = diff.get("nodes", {})
    relationship_diff = diff.get("relationships", {})
    summary = plan.summary or {}
    totals = summary.get("totals", {})
    scope = plan.scope or {}
    context = PageProcessor().decorate(
        {
            "plan": plan,
            "summary": summary,
            "totals": totals,
            "scope_labels": scope.get("labels", []),
            "node_create": node_diff.get("create", []),
            "node_update": node_diff.get("update", []),
            "node_delete": node_diff.get("delete", []),
            "node_ignored": node_diff.get("ignored", []),
            "relationship_create": relationship_diff.get("create", []),
            "relationship_update": relationship_diff.get("update", []),
            "relationship_delete": relationship_diff.get("delete", []),
            "relationship_ignored": relationship_diff.get("ignored", []),
            "has_changes": bool(plan.total_changes),
        },
        request,
    )
    return render(request, "ravioli/projection_plan_detail.html", context)


@require_POST
@superuser_required
def apply_projection_plan_view(request, plan_id):
    from .connection import Neo4jClient, is_enabled
    from .planner import apply_projection_plan

    plan = get_object_or_404(GraphProjectionPlan, pk=plan_id)

    if plan.status != GraphProjectionPlan.STATUS_READY:
        messages.warning(request, "Only ready projection plans can be applied.")
        return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)

    client = Neo4jClient()
    try:
        apply_projection_plan(client, plan)
    except Exception as exc:
        plan.status = GraphProjectionPlan.STATUS_FAILED
        plan.error = str(exc)
        plan.save(update_fields=["status", "error", "updated_at"])
        messages.error(request, f"Could not apply projection plan: {exc}")
    else:
        messages.success(request, f"Projection plan #{plan.pk} applied.")
    finally:
        client.close()

    return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)


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
