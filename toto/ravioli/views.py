import json

from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from django.db import models as _models

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

    # Metering: cypher query + row count
    from toto.metering.utils import safe_record_usage as _m
    _user = request.user if getattr(request, "user", None) and request.user.is_authenticated else None
    _sub = (
        {"subject_type": "auth.User", "subject_id": str(_user.pk), "subject_label": _user.username}
        if _user else {"subject_type": "system", "subject_id": "ravioli"}
    )
    _run_key = f"ravioli.cypher_query:view:{selected_query.pk}:{timezone.now().strftime('%Y%m%dT%H%M%S')}"
    _m(
        metric_code="ravioli.cypher_query",
        quantity=1,
        unit="query",
        source_type="ravioli.CypherQuery",
        source_id=str(selected_query.pk),
        source_label=selected_query.name,
        idempotency_key=_run_key,
        metadata={"node_count": len(nodes), "edge_count": len(edges)},
        **_sub,
    )
    _row_count = len(nodes) + len(edges)
    if _row_count:
        _m(
            metric_code="ravioli.cypher_row",
            quantity=_row_count,
            unit="row",
            source_type="ravioli.CypherQuery",
            source_id=str(selected_query.pk),
            source_label=selected_query.name,
            idempotency_key=f"ravioli.cypher_row:view:{selected_query.pk}:{timezone.now().strftime('%Y%m%dT%H%M%S')}",
            **_sub,
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

    # ── Tariff balance check ──────────────────────────────────────────────────
    _ravioli_tariff = None
    try:
        from toto.tariffs.charge import (
            InsufficientBalanceError, check_user_can_act, get_tariff_for_user,
        )
        _ravioli_tariff = get_tariff_for_user(request.user, "ravioli")
        if _ravioli_tariff:
            check_user_can_act(request.user, _ravioli_tariff, "ravioli.cypher_query", 1)
    except InsufficientBalanceError as _exc:
        return JsonResponse({
            "error": str(_exc),
            "insufficient_balance": True,
            "asset": _exc.asset_name,
            "needed": str(_exc.needed_display),
            "have": str(_exc.have_display),
        }, status=402)
    except Exception:
        _ravioli_tariff = None

    try:
        run = _trigger_workflow(
            "ravioli-run-cypher-query",
            input_data={"data": {"query_id": selected_query.pk}},
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)

    # ── Charge after successful trigger ──────────────────────────────────────
    try:
        if _ravioli_tariff:
            from toto.tariffs.charge import charge_user as _charge
            _charge(request.user, _ravioli_tariff, "ravioli.cypher_query", 1,
                    source_type="ravioli.CypherQuery", source_id=str(selected_query.pk))
    except Exception:
        pass

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


@superuser_required
def graph_analysis_view(request):
    from toto.vault.models import Bucket, VaultDirectory
    from toto.workflows.models import Workflow, WorkflowNodeRun, WorkflowRun

    queries = list(CypherQuery.objects.all().order_by("name"))
    workflows = list(
        Workflow.objects.filter(
            _models.Q(slug__startswith="graph-analysis-")
            | _models.Q(nodes__task_name__in=[
                "ravioli_prepare_graph_analysis",
                "ravioli_save_graph_analysis_output",
            ])
        ).distinct().order_by("name")
    )
    buckets = list(Bucket.objects.all().order_by("name"))
    directories = list(
        VaultDirectory.objects.select_related("bucket").order_by("bucket__name", "name")
    )

    recent_runs = list(
        WorkflowRun.objects
        .filter(workflow__slug__startswith="graph-analysis-")
        .select_related("workflow")
        .order_by("-created_at")[:20]
    )

    file_data_by_run: dict[int, dict] = {}
    completed_ids = [r.pk for r in recent_runs if r.status == WorkflowRun.COMPLETED]
    if completed_ids:
        for nr in WorkflowNodeRun.objects.filter(
            workflow_run_id__in=completed_ids,
            node__task_name="ravioli_save_graph_analysis_output",
            status=WorkflowNodeRun.COMPLETED,
        ).select_related("node"):
            od = (nr.output_data or {}).get("data") or {}
            if od.get("vault_file_id"):
                file_data_by_run[nr.workflow_run_id] = od

    # Annotate runs so the template can access file data without a custom filter
    runs_with_files = [
        (run, file_data_by_run.get(run.pk))
        for run in recent_runs
    ]

    buckets_json = json.dumps([{"id": b.id, "name": b.name} for b in buckets])
    directories_json = json.dumps([
        {"id": d.id, "bucket_id": d.bucket_id, "path": d.full_path()}
        for d in directories
    ])

    context = PageProcessor().decorate(
        {
            "queries": queries,
            "workflows": workflows,
            "buckets": buckets,
            "buckets_json": buckets_json,
            "directories_json": directories_json,
            "formats": ["json", "yaml", "csv"],
            "runs_with_files": runs_with_files,
        },
        request,
    )
    return render(request, "ravioli/graph_analysis.html", context)


@require_POST
@superuser_required
def start_graph_analysis_view(request):
    query_id = request.POST.get("query_id")
    workflow_slug = request.POST.get("workflow_slug")
    bucket_id = request.POST.get("bucket_id")
    directory_id = request.POST.get("directory_id") or None
    fmt = request.POST.get("format", "json")
    title = request.POST.get("title", "").strip() or None

    errors = []
    if not query_id:
        errors.append("Select a Cypher query.")
    if not workflow_slug:
        errors.append("Select a workflow.")
    if not bucket_id:
        errors.append("Select an output bucket.")
    if fmt not in ("json", "yaml", "csv"):
        errors.append(f"Invalid format: {fmt!r}.")
    if errors:
        return JsonResponse({"error": " ".join(errors)}, status=400)

    if not celery_available():
        return JsonResponse({"error": "No Celery worker is running."}, status=503)

    input_data: dict = {
        "data": {
            "query_id": int(query_id),
            "owner_id": request.user.pk,
            "bucket_id": int(bucket_id),
            "format": fmt,
        }
    }
    if directory_id:
        input_data["data"]["directory_id"] = int(directory_id)
    if title:
        input_data["data"]["title"] = title

    try:
        run = _trigger_workflow(workflow_slug, input_data=input_data)
    except RuntimeError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    return JsonResponse({"run_id": run.pk})


@require_GET
@superuser_required
def graph_analysis_status_view(request, run_id):
    from toto.workflows.models import WorkflowNodeRun, WorkflowRun

    run = get_object_or_404(WorkflowRun.objects.select_related("workflow"), pk=run_id)

    node_runs = list(run.node_runs.select_related("node").all())
    total = run.workflow.nodes.count()
    done = sum(
        1 for nr in node_runs
        if nr.status in (
            WorkflowNodeRun.COMPLETED,
            WorkflowNodeRun.FAILED,
            WorkflowNodeRun.SKIPPED,
        )
    )
    percent = int(done / total * 100) if total else 0
    if run.status == WorkflowRun.COMPLETED:
        percent = 100

    vault_file_id = None
    download_url = None
    for nr in node_runs:
        if (
            nr.node.task_name == "ravioli_save_graph_analysis_output"
            and nr.status == WorkflowNodeRun.COMPLETED
        ):
            file_data = (nr.output_data or {}).get("data") or {}
            vault_file_id = file_data.get("vault_file_id")
            download_url = file_data.get("download_url")
            break

    error = None
    for nr in node_runs:
        if nr.status == WorkflowNodeRun.FAILED:
            error = nr.error or f"Node {nr.node.label!r} failed."
            break

    return JsonResponse({
        "status": run.status,
        "percent": percent,
        "completed_nodes": done,
        "total_nodes": total,
        "vault_file_id": vault_file_id,
        "download_url": download_url,
        "error": error,
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
