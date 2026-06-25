import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.shortcuts import redirect, render, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from django.db import models as _models

from .models import CypherQuery
from toto.celery_utils import celery_available
from toto.ui import PageProcessor


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


@superuser_required
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

    from toto.quota import usage_summary
    quota_data = usage_summary("ravioli", "auth.User", str(request.user.pk)) if request.user.is_authenticated else []

    # Buckets / directories for the "Export → NeoJSON → Vault" dialog.
    from toto.vault.models import Bucket, VaultDirectory
    buckets = list(Bucket.objects.all().order_by("name"))
    directories = list(
        VaultDirectory.objects.select_related("bucket").order_by("bucket__name", "name")
    )
    buckets_json = json.dumps([{"id": b.id, "name": b.name} for b in buckets])
    directories_json = json.dumps([
        {"id": d.id, "bucket_id": d.bucket_id, "path": d.full_path()}
        for d in directories
    ])

    context = PageProcessor().decorate(
        {
            "queries": queries,
            "queries_json": queries_json,
            "run_stats": run_stats,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "quota_data": quota_data,
            "buckets_json": buckets_json,
            "directories_json": directories_json,
        },
        request,
    )
    return render(request, "ravioli/query_unified.html", context)


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


@superuser_required
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

    from toto.quota import record_usage as _ru
    _stype = "auth.User" if request.user.is_authenticated else "system"
    _sid = str(request.user.pk) if request.user.is_authenticated else "ravioli"
    _src_type = "ravioli.CypherQuery"
    _src_id = str(selected_query.pk)
    _ru("ravioli", "graph.query", 1, _stype, _sid,
        source_type=_src_type, source_id=_src_id,
        idempotency_key=f"ravioli.query.view:{selected_query.pk}:{timezone.now().strftime('%Y%m%dT%H%M')}")
    _row_count = len(nodes) + len(edges)
    if _row_count:
        _ru("ravioli", "graph.rows", _row_count, _stype, _sid,
            source_type=_src_type, source_id=_src_id)

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
@superuser_required
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
@superuser_required
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


# ---------------------------------------------------------------------------
# NeoJSON: export a query result to the vault (the editor lives in toto.neo_editor)
# ---------------------------------------------------------------------------

@require_POST
@superuser_required
def export_query_neojson_view(request, query_id):
    """Serialize a Cypher query's cached graph into a vault file.

    Driven by the export dialog: the caller chooses ``bucket_id`` (required),
    an optional ``directory_id``, and a ``name``. The file keeps a JSON-friendly
    extension but is stored with ``file_type='neojson'`` so it opens in the
    neo_editor graph editor. ``bucket_id`` is auto-picked only as a fallback.
    """
    import os

    from django.core.files.base import ContentFile
    from django.urls import reverse
    from django.utils import timezone
    from django.utils.text import slugify

    from toto.vault.models import Bucket, VaultDirectory, VaultFile
    from . import neojson
    from .graph_analysis import load_query_graph

    selected_query = get_object_or_404(CypherQuery, pk=query_id)

    # Target bucket: chosen in the dialog; fall back to general → owned → any.
    bucket_id = request.POST.get("bucket_id")
    if bucket_id:
        bucket = get_object_or_404(Bucket, pk=bucket_id)
    else:
        bucket = (
            Bucket.objects.filter(slug="general").first()
            or Bucket.objects.filter(owner=request.user).first()
            or Bucket.objects.first()
        )
    if bucket is None:
        return JsonResponse({"error": "No vault bucket available to save into."}, status=400)

    directory = None
    directory_id = request.POST.get("directory_id") or None
    if directory_id:
        directory = get_object_or_404(VaultDirectory, pk=directory_id, bucket=bucket)

    try:
        nodes, edges = load_query_graph(query_id)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    graph = neojson.from_ravioli(
        nodes,
        edges,
        metadata={
            "generated_at": timezone.now().isoformat(),
            "source": f"ravioli:cypher_query/{query_id}",
        },
    )
    content = neojson.dumps(graph).encode("utf-8")

    # Filename from the dialog (defaults to "<query>.json"). Stored as neojson
    # regardless of the extension; the vault key is kept unique in the bucket.
    raw_name = (request.POST.get("name") or "").strip()
    if not raw_name:
        raw_name = f"{slugify(selected_query.name) or 'query'}.json"
    stem, ext = os.path.splitext(raw_name)
    ext = ext or ".json"
    key_base = slugify(stem) or "query"
    key = key_base
    if VaultFile.objects.filter(bucket=bucket, key=key).exists():
        key = f"{key_base}-{timezone.now().strftime('%H%M%S')}"
    filename = f"{key}{ext}"
    title = raw_name if raw_name.lower().endswith(ext.lower()) else f"{raw_name}{ext}"

    vault_file = VaultFile(
        owner=request.user,
        title=title,
        bucket=bucket,
        directory=directory,
        file_type="neojson",
        is_public=False,
        key=key,
    )
    vault_file.file.save(filename, ContentFile(content), save=False)
    vault_file.save()

    return JsonResponse({
        "vault_file_id": vault_file.pk,
        "editor_url": reverse("neo_editor:neojson_editor", args=[vault_file.pk]),
        "filename": title,
        "bucket": bucket.name,
        "node_count": len(graph.nodes),
        "relationship_count": len(graph.relationships),
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
    if fmt not in ("json", "yaml", "csv", "neojson"):
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


@login_required
def search_view(request):
    from django.conf import settings as _settings

    from .services.search import (
        MODE_KEYWORD,
        MODE_SEMANTIC,
        SearchUnavailableError,
        resolve_mode,
        run_search,
        semantic_available,
    )

    q = request.GET.get("q", "").strip()
    sem_avail = semantic_available()
    # No default/auto mode — the user picks. Keyword is the safe fallback for an
    # empty/unknown mode, and a bookmarked ?mode=semantic must not stick when
    # embeddings are unavailable.
    mode = resolve_mode(request.GET.get("mode", MODE_KEYWORD))
    if mode == MODE_SEMANTIC and not sem_avail:
        mode = MODE_KEYWORD
    exact = request.GET.get("exact") == "1"
    exec_mode = request.GET.get("exec", "celery")  # "celery" | "direct"
    try:
        limit = max(1, min(200, int(request.GET.get("limit", 25))))
    except (ValueError, TypeError):
        limit = 25

    results = []
    error = None
    searched = False
    wf_run_id = None

    # Search metadata — populated on direct execution; Celery path fills via status polling
    _empty_meta = {
        "requested_mode": mode,
        "effective_mode": mode,
        "fallback_used": False,
        "fallback_reason": None,
        "semantic_available": sem_avail,
    }
    search_meta = dict(_empty_meta)

    if q:
        searched = True
        if exec_mode == "direct":
            try:
                sr = run_search(q, mode=mode, limit=limit, exact=exact)
                results = sr["results"]
                search_meta = {k: sr[k] for k in _empty_meta}
            except SearchUnavailableError as exc:
                error = str(exc)
        else:
            # Celery path — metadata arrives via polling the status endpoint
            try:
                wf_run = _trigger_workflow(
                    "ravioli-graph-search",
                    input_data={"data": {
                        "q": q, "mode": mode, "limit": limit, "exact": exact,
                    }},
                )
                wf_run_id = wf_run.pk
            except RuntimeError as exc:
                error = str(exc)

    # Controls visibility: staff or DEBUG users see the Celery/Direct toggle
    show_exec_controls = (
        (request.user.is_authenticated and request.user.is_staff)
        or bool(getattr(_settings, "DEBUG", False))
    )

    # The user picks the method explicitly — no auto/default mode. Semantic only
    # appears when an embedding backend is actually available.
    search_method_choices = [
        ("keyword",  "Keyword",       "fa-solid fa-text-size",
         "Searches name, description, and text fields."),
        ("fulltext", "Full-text",     "fa-solid fa-layer-group",
         "Lucene full-text index on Chunk nodes."),
    ]
    if sem_avail:
        search_method_choices.append(
            ("semantic", "Semantic", "fa-solid fa-brain",
             "Vector similarity search over graph embeddings."),
        )

    context = PageProcessor().decorate(
        {
            "q": q,
            "mode": mode,
            "exact": exact,
            "exec_mode": exec_mode,
            "limit": limit,
            "results": results,
            "error": error,
            "searched": searched,
            "result_count": len(results),
            "wf_run_id": wf_run_id,
            "show_exec_controls": show_exec_controls,
            "search_meta": search_meta,
            "search_meta_json": json.dumps(search_meta).replace("</", "<\\/"),
            "search_method_choices": search_method_choices,
            "initial_results_json": json.dumps(results).replace("</", "<\\/"),
        },
        request,
    )
    return render(request, "ravioli/search.html", context)


@require_GET
@login_required
def search_status_view(request, run_id):
    """Poll a ravioli-graph-search WorkflowRun for status + results."""
    from toto.workflows.models import WorkflowRun

    try:
        run = WorkflowRun.objects.get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return JsonResponse({"status": "expired"}, status=404)

    wf_status = run.status  # pending / running / completed / failed / cancelled

    if wf_status == WorkflowRun.COMPLETED:
        node_run = run.node_runs.order_by("id").last()
        output = (node_run.output_data or {}) if node_run else {}
        data = output.get("data") or {}
        results = data.get("results", [])
        try:
            return JsonResponse({
                "status": "done",
                "results": results,
                "requested_mode": data.get("requested_mode", "keyword"),
                "effective_mode": data.get("effective_mode", "keyword"),
                "fallback_used": bool(data.get("fallback_used", False)),
                "fallback_reason": data.get("fallback_reason"),
                "semantic_available": bool(data.get("semantic_available", False)),
            })
        except (TypeError, ValueError) as exc:
            return JsonResponse({"status": "error", "error": f"Serialization failed: {exc}"}, status=500)

    if wf_status == WorkflowRun.FAILED:
        node_run = run.node_runs.order_by("id").last()
        err = (node_run.error if node_run else None) or "Search workflow failed."
        return JsonResponse({"status": "error", "error": err})

    if wf_status == "cancelled":
        return JsonResponse({"status": "error", "error": "Run was cancelled."})

    # still pending or running
    return JsonResponse({"status": "running", "wf_run_url": f"/workflows/runs/{run.pk}/"})


# ---------------------------------------------------------------------------
# Ask AI — GraphRAG over the graph (Celery task wrapped in a workflow)
# ---------------------------------------------------------------------------

@login_required
@superuser_required
def graphrag_describe_view(request):
    """Render the 'Ask AI' tab (chat over the graph via GraphRAG)."""
    from .predefined_tasks import GRAPHRAG_DESCRIBE_PROMPT

    context = PageProcessor().decorate({"describe_prompt": GRAPHRAG_DESCRIBE_PROMPT}, request)
    return render(request, "ravioli/graphrag.html", context)


@require_POST
@superuser_required
def start_graphrag_describe_view(request):
    """Kick off the GraphRAG workflow (Celery) and return the run id to poll."""
    from django.apps import apps as django_apps

    from .connection import is_enabled

    if not is_enabled():
        return JsonResponse({"error": "RAVIOLI_ENABLED is False — cannot run GraphRAG."}, status=503)
    if not django_apps.is_installed("toto.sabbia"):
        return JsonResponse({"error": "Steven (toto.sabbia) is not installed."}, status=503)
    if not celery_available():
        return JsonResponse({"error": "No Celery worker is running."}, status=503)
    question = (request.POST.get("question") or "").strip()
    try:
        run = _trigger_workflow(
            "ravioli-graphrag-describe",
            input_data={"data": {"question": question, "top_k": 8, "text2cypher": True}},
        )
    except RuntimeError as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    return JsonResponse({"run_id": run.pk, "wf_run_url": f"/workflows/runs/{run.pk}/"})


@require_GET
@superuser_required
def graphrag_describe_status_view(request, run_id):
    """Poll a ravioli-graphrag-describe WorkflowRun for Steven's answer."""
    from toto.workflows.models import WorkflowRun

    try:
        run = WorkflowRun.objects.get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return JsonResponse({"status": "expired"}, status=404)

    if run.status == WorkflowRun.COMPLETED:
        node_run = run.node_runs.order_by("id").last()
        data = ((node_run.output_data or {}) if node_run else {}).get("data") or {}
        return JsonResponse({
            "status": "done",
            "answer": data.get("answer", ""),
            "context": data.get("context", ""),
            "graph": data.get("graph") or {"nodes": [], "edges": []},
            "agent": data.get("agent", ""),
            "question": data.get("question", ""),
        })
    if run.status == WorkflowRun.FAILED:
        node_run = run.node_runs.order_by("id").last()
        return JsonResponse({"status": "error",
                             "error": (node_run.error if node_run else None) or "GraphRAG workflow failed."})
    if run.status == "cancelled":
        return JsonResponse({"status": "error", "error": "Run was cancelled."})
    return JsonResponse({"status": "running", "wf_run_url": f"/workflows/runs/{run.pk}/"})


# ---------------------------------------------------------------------------
# Per-object "Export to graph" — preview the 1-hop slice, then apply the diff
# ---------------------------------------------------------------------------

class _ExportError(Exception):
    """Carries a user-facing message for a failed export-target resolution."""


def _resolve_export_target(app_label, model_name):
    """Return ``(model, label, uuid_field)`` or raise ``_ExportError(message)``.

    Constructs a client-less ``GraphExporter`` purely for the label registry;
    no Neo4j I/O happens here.
    """
    from django.apps import apps as django_apps

    from .connection import is_enabled
    from .graph_export import GraphExporter, is_app_excluded

    if not is_enabled():
        raise _ExportError("Graph database is disabled (RAVIOLI_ENABLED is False).")
    if is_app_excluded(app_label):
        raise _ExportError("This kind of record is not exported to the graph.")
    try:
        model = django_apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        raise _ExportError("Unknown model for graph export.")

    mapping = GraphExporter(None).label_for_model(model)
    if mapping is None:
        raise _ExportError(
            f"{model._meta.verbose_name.title()} is not mapped to the graph."
        )
    label, uuid_field = mapping
    return model, label, uuid_field


@login_required
def graph_export_preview(request, app_label, model_name, object_uuid):
    """Cytoscape preview of the object + its 1-hop neighbours before applying."""
    from .connection import Neo4jClient
    from .graph_export import GraphExporter

    back = request.META.get("HTTP_REFERER") or "/"
    try:
        model, label, uuid_field = _resolve_export_target(app_label, model_name)
    except _ExportError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    try:
        obj = model.objects.get(**{uuid_field: object_uuid})
    except model.DoesNotExist:
        messages.error(request, "That object no longer exists.")
        return redirect(back)

    client = Neo4jClient()
    try:
        nodes, edges, summary = GraphExporter(client).preview(label, obj)
    finally:
        client.close()

    context = PageProcessor().decorate(
        {
            "object_label": str(obj),
            "graph_label": label,
            "nodes_json": json.dumps(nodes),
            "edges_json": json.dumps(edges),
            "summary": summary,
            "apply_url": reverse(
                "ravioli:graph_export_apply", args=[app_label, model_name, object_uuid]
            ),
            "back_url": back,
        },
        request,
    )
    return render(request, "ravioli/graph_export_preview.html", context)


@require_POST
@login_required
def graph_export_apply(request, app_label, model_name, object_uuid):
    """Apply the 1-hop slice to Neo4j (checksum-gated, history-preserving)."""
    from .connection import Neo4jClient
    from .graph_export import GraphExporter

    back = request.POST.get("back") or request.META.get("HTTP_REFERER") or "/"
    try:
        model, label, uuid_field = _resolve_export_target(app_label, model_name)
    except _ExportError as exc:
        messages.error(request, str(exc))
        return redirect(back)
    try:
        obj = model.objects.get(**{uuid_field: object_uuid})
    except model.DoesNotExist:
        messages.error(request, "That object no longer exists.")
        return redirect(back)

    client = Neo4jClient()
    try:
        result = GraphExporter(client).apply(label, obj)
    except Exception as exc:
        messages.error(request, f"Graph export failed: {exc}")
        return redirect(back)
    finally:
        client.close()

    parts = [
        f"{result[k]} {k}"
        for k in ("created", "updated", "unchanged")
        if result[k]
    ]
    detail = ", ".join(parts) or "no changes"
    messages.success(request, f"Exported {label} to the graph ({detail}).")
    return redirect(back)


# ---------------------------------------------------------------------------
# Review-then-apply plans: "Sync all to graph" and "Prune history" share one
# review modal, one apply endpoint, and the graph_plans service.
# ---------------------------------------------------------------------------

def _plan_response(plan):
    from .services import graph_plans

    return JsonResponse(graph_plans.plan_payload(
        plan,
        reverse("ravioli:graph_plan_apply", args=[plan.id]),
        reverse("sql_neo4j_sync:projection_plan_detail", args=[plan.id]),
    ))


@require_POST
@superuser_required
def graph_sync_plan(request):
    """Compute (but don't apply) the full SQL→Neo4j diff for every label."""
    from .connection import Neo4jClient, is_enabled
    from .services import graph_plans

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot connect to Neo4j."}, status=503
        )
    client = Neo4jClient()
    try:
        plan = graph_plans.create_sync_plan(client)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    finally:
        client.close()
    return _plan_response(plan)


@require_POST
@superuser_required
def graph_prune_plan(request):
    """Compute (but don't apply) a plan that prunes old :HISTORICAL snapshots.

    ``keep`` (POST) = newest snapshots to keep per node (defaults to the setting).
    """
    from .connection import Neo4jClient, is_enabled
    from .services import graph_plans

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot connect to Neo4j."}, status=503
        )
    client = Neo4jClient()
    try:
        plan = graph_plans.create_prune_plan(client, keep=request.POST.get("keep"))
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    finally:
        client.close()
    return _plan_response(plan)


@require_POST
@superuser_required
def graph_plan_apply(request, plan_id):
    """Apply any previously computed plan — sync or prune (the approval step)."""
    from toto.sql_neo4j_sync.models import GraphProjectionPlan

    from .connection import Neo4jClient, is_enabled
    from .services import graph_plans

    plan = get_object_or_404(GraphProjectionPlan, pk=plan_id)
    if plan.status != GraphProjectionPlan.STATUS_READY:
        return JsonResponse(
            {"error": f"Plan #{plan.id} is '{plan.status}', not ready to apply."},
            status=400,
        )
    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot connect to Neo4j."}, status=503
        )

    staged = request.POST.get("staged") in ("1", "true", "on")
    client = Neo4jClient()
    try:
        graph_plans.apply_plan(client, plan, staged=staged)
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    finally:
        client.close()

    return JsonResponse({"status": "applied", "total_changes": plan.total_changes})


@require_GET
@login_required
def graph_health_view(request):
    """Lightweight connectivity probe for the 'Neo4j is not running' banner."""
    from .connection import is_alive, is_enabled

    response = JsonResponse({"enabled": is_enabled(), "alive": is_alive()})
    # Never cache — a stale probe would keep the banner up after Neo4j recovers.
    response["Cache-Control"] = "no-store, max-age=0"
    return response


# ---------------------------------------------------------------------------
# History tab — view :HISTORICAL snapshots, then prune via the shared review flow
# ---------------------------------------------------------------------------

@require_GET
@superuser_required
def history_view(request):
    """The History tab page: inspect kept :HISTORICAL snapshots and prune them."""
    from .services.graph_plans import default_keep

    context = PageProcessor().decorate({"default_keep": default_keep()}, request)
    return render(request, "ravioli/history.html", context)


@require_GET
@superuser_required
def history_data(request):
    """JSON history graph: each canonical node + its :HISTORICAL snapshots (with depth)."""
    from .connection import Neo4jClient, is_enabled

    if not is_enabled():
        return JsonResponse(
            {"error": "RAVIOLI_ENABLED is False — cannot connect to Neo4j."}, status=503
        )

    client = Neo4jClient()
    try:
        # Anchor on the snapshots themselves (grouped by the canonical they came
        # from, `prev_uuid`) so orphans — snapshots whose canonical was deleted by
        # a later sync, dropping the :HISTORICAL edge — are still listed.
        rows = client.run_cypher(
            "MATCH (h) WHERE h._historical = true "
            "WITH h ORDER BY h._archived_at DESC "
            "WITH h._prev_uuid AS pk, collect(h) AS hs "
            "UNWIND range(0, size(hs) - 1) AS i "
            "WITH pk, hs[i] AS h, i AS depth "
            "OPTIONAL MATCH (c) WHERE c.uuid = pk AND coalesce(c._historical, false) = false "
            "RETURN labels(h) AS h_labels, h.uuid AS h_uuid, pk AS prev_uuid, "
            "(CASE WHEN c IS NULL THEN [] ELSE labels(c) END) AS c_labels, depth, "
            "properties(h) AS h_props, "
            "(CASE WHEN c IS NULL THEN {} ELSE properties(c) END) AS c_props "
            "LIMIT 2000"
        )
    except Exception as exc:
        return JsonResponse({"error": str(exc)}, status=500)
    finally:
        client.close()

    def label0(labels):
        return (labels or [""])[0]

    def jsonable(props):
        # Neo4j temporal/spatial values aren't JSON-serialisable; stringify them.
        out = {}
        for k, v in (props or {}).items():
            out[k] = v if isinstance(v, (str, int, float, bool, type(None))) else str(v)
        return out

    nodes, edges = {}, []
    for row in rows:
        h_label = label0(row["h_labels"])
        present = bool(row["c_labels"])
        c_label = label0(row["c_labels"]) or h_label   # canonical shares the label
        c_uuid, h_uuid = str(row["prev_uuid"]), str(row["h_uuid"])
        c_id, h_id = f"{c_label}:{c_uuid}", f"{h_label}:{h_uuid}"
        nodes.setdefault(c_id, {
            "id": c_id, "label": c_label, "uuid": c_uuid,
            "kind": "canonical", "present": present, "props": jsonable(row["c_props"]),
        })
        nodes[h_id] = {
            "id": h_id, "label": h_label, "uuid": h_uuid,
            "kind": "snapshot", "depth": row["depth"], "props": jsonable(row["h_props"]),
        }
        edges.append({"id": f"hist:{c_id}->{h_id}", "source": c_id, "target": h_id})

    return JsonResponse({"nodes": list(nodes.values()), "edges": edges})
