import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .models import CypherQuery, GraphProjectionPlan
from toto.celery_utils import celery_available
from toto.ui import PageProcessor


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def query_unified_view(request):
    from django.db.models import Count, Q

    from .models import CypherQueryResult
    from toto.workflows.models import WorkflowRun

    queries = list(CypherQuery.objects.all().order_by("name"))

    results_by_query = {
        r.query_id: r
        for r in CypherQueryResult.objects.filter(query__in=queries)
    }

    queries_json = json.dumps([
        {
            "id": q.id,
            "name": q.name,
            "description": q.description,
            "query": q.query,
            "last_run_at": (
                results_by_query[q.id].last_run_at.isoformat()
                if q.id in results_by_query and results_by_query[q.id].last_run_at
                else None
            ),
            "cached_node_count": (
                len(results_by_query[q.id].result_nodes or [])
                if q.id in results_by_query
                else None
            ),
            "cached_edge_count": (
                len(results_by_query[q.id].result_edges or [])
                if q.id in results_by_query
                else None
            ),
        }
        for q in queries
    ])

    run_stats = WorkflowRun.objects.filter(
        workflow__slug="ravioli-run-cypher-query"
    ).aggregate(
        total=Count("id"),
        succeeded=Count("id", filter=Q(status=WorkflowRun.COMPLETED)),
        failed=Count("id", filter=Q(status=WorkflowRun.FAILED)),
    )

    total_nodes = sum(len(r.result_nodes or []) for r in results_by_query.values())
    total_edges = sum(len(r.result_edges or []) for r in results_by_query.values())

    context = PageProcessor().decorate(
        {
            "queries": queries,
            "queries_json": queries_json,
            "run_stats": run_stats,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
        },
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
    from .connection import is_enabled
    from .loader import load_all_configs, validate_configs

    selected_labels = request.POST.getlist("models")
    if not selected_labels:
        messages.warning(request, "Select at least one graph label.")
        return redirect("ravioli:projection_sync")

    errors = validate_configs(load_all_configs())
    if errors:
        messages.error(request, "Graph config is invalid: " + "; ".join(errors))
        return redirect("ravioli:projection_sync")

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("ravioli:projection_sync")

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot generate plan.")
        return redirect("ravioli:projection_sync")

    try:
        run = _trigger_workflow(
            "ravioli-generate-plan",
            input_data={"data": {"labels": selected_labels}},
        )
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("ravioli:projection_sync")

    messages.success(request, f"Plan generation started (run #{run.pk}) for {len(selected_labels)} labels.")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


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
    from .connection import is_enabled

    plan = get_object_or_404(GraphProjectionPlan, pk=plan_id)

    if plan.status != GraphProjectionPlan.STATUS_READY:
        messages.warning(request, "Only ready projection plans can be applied.")
        return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot apply plan.")
        return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)

    try:
        run = _trigger_workflow(
            "ravioli-apply-plan",
            input_data={"data": {"plan_id": plan.pk}},
        )
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("ravioli:projection_plan_detail", plan_id=plan.pk)

    messages.success(request, f"Plan #{plan.pk} apply started (run #{run.pk}).")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


def _trigger_workflow(slug: str, input_data: dict | None = None) -> "WorkflowRun":
    from toto.workflows.models import Workflow, WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    wf = Workflow.objects.filter(slug=slug).first()
    if wf is None:
        raise RuntimeError(
            f"Workflow '{slug}' not found — run ingress_ravioli to create it."
        )
    run = WorkflowRun.objects.create(workflow=wf, input_data=input_data or {})
    start_workflow_run_task.delay(run.pk)
    return run


@require_POST
@superuser_required
def full_sync_view(request):
    from .connection import is_enabled

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("ravioli:projection_sync")

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot run full sync.")
        return redirect("ravioli:projection_sync")

    try:
        run = _trigger_workflow("ravioli-sync")
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("ravioli:projection_sync")

    messages.success(request, f"Full sync started (run #{run.pk}) — generate + apply for all labels.")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


@require_POST
@superuser_required
def clear_db_view(request):
    from .connection import is_enabled

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("ravioli:projection_sync")

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot clear database.")
        return redirect("ravioli:projection_sync")

    try:
        run = _trigger_workflow("ravioli-clear-db")
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("ravioli:projection_sync")

    messages.warning(request, f"Clear DB started (run #{run.pk}) — deleting all ravioli-owned data.")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


def query_graph_data(request, query_id):
    from django.utils import timezone

    from .connection import Neo4jClient, is_enabled
    from .models import CypherQueryResult

    selected_query = CypherQuery.objects.get(id=query_id)

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

    CypherQueryResult.objects.update_or_create(
        query=selected_query,
        defaults={
            "result_nodes": nodes,
            "result_edges": edges,
            "last_run_at": timezone.now(),
            "error": "",
        },
    )

    return JsonResponse({
        "nodes": nodes,
        "edges": edges,
        "query": selected_query.query,
        "selected_query": {
            "id": selected_query.id,
            "name": selected_query.name,
            "description": selected_query.description,
        },
    })


@require_POST
def run_cypher_query_view(request, query_id):
    selected_query = get_object_or_404(CypherQuery, pk=query_id)
    try:
        run = _trigger_workflow(
            "ravioli-run-cypher-query",
            input_data={"data": {"query_id": selected_query.pk}},
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"run_id": run.pk})


@require_GET
def query_cached_data(request, query_id):
    from .models import CypherQueryResult

    selected_query = get_object_or_404(CypherQuery, pk=query_id)
    result = CypherQueryResult.objects.filter(query=selected_query).first()
    if result is None or result.result_nodes is None:
        return JsonResponse({"error": "No cached result available."}, status=404)
    return JsonResponse({
        "nodes": result.result_nodes,
        "edges": result.result_edges or [],
        "query": selected_query.query,
        "last_run_at": result.last_run_at.isoformat() if result.last_run_at else None,
        "selected_query": {
            "id": selected_query.id,
            "name": selected_query.name,
            "description": selected_query.description,
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
