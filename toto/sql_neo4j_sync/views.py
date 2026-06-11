import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from toto.celery_utils import celery_available
from toto.ui import PageProcessor

from .models import GraphProjectionPlan


def superuser_required(view_func):
    return user_passes_test(lambda u: u.is_active and u.is_superuser)(view_func)


def _trigger_workflow(slug: str, input_data: dict | None = None) -> "WorkflowRun":
    from toto.workflows.models import Workflow, WorkflowRun
    from toto.workflows.tasks import start_workflow_run_task

    wf = Workflow.objects.filter(slug=slug).first()
    if wf is None:
        raise RuntimeError(
            f"Workflow '{slug}' not found — run ingress_sql_neo4j_sync to create it."
        )
    run = WorkflowRun.objects.create(workflow=wf, input_data=input_data or {})
    start_workflow_run_task.delay(run.pk)
    return run


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
    return render(request, "sql_neo4j_sync/projection_sync.html", context)


@require_POST
@superuser_required
def create_projection_plan(request):
    from toto.ravioli.connection import is_enabled

    from .loader import load_all_configs, validate_configs

    selected_labels = request.POST.getlist("models")
    if not selected_labels:
        messages.warning(request, "Select at least one graph label.")
        return redirect("sql_neo4j_sync:projection_sync")

    errors = validate_configs(load_all_configs())
    if errors:
        messages.error(request, "Graph config is invalid: " + "; ".join(errors))
        return redirect("sql_neo4j_sync:projection_sync")

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("sql_neo4j_sync:projection_sync")

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot generate plan.")
        return redirect("sql_neo4j_sync:projection_sync")

    try:
        run = _trigger_workflow(
            "ravioli-generate-plan",
            input_data={"data": {"labels": selected_labels}},
        )
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("sql_neo4j_sync:projection_sync")

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
    return render(request, "sql_neo4j_sync/projection_plan_detail.html", context)


@require_POST
@superuser_required
def apply_projection_plan_view(request, plan_id):
    from toto.ravioli.connection import is_enabled

    plan = get_object_or_404(GraphProjectionPlan, pk=plan_id)

    if plan.status != GraphProjectionPlan.STATUS_READY:
        messages.warning(request, "Only ready projection plans can be applied.")
        return redirect("sql_neo4j_sync:projection_plan_detail", plan_id=plan.pk)

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("sql_neo4j_sync:projection_plan_detail", plan_id=plan.pk)

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot apply plan.")
        return redirect("sql_neo4j_sync:projection_plan_detail", plan_id=plan.pk)

    try:
        run = _trigger_workflow(
            "ravioli-apply-plan",
            input_data={"data": {"plan_id": plan.pk}},
        )
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("sql_neo4j_sync:projection_plan_detail", plan_id=plan.pk)

    messages.success(request, f"Plan #{plan.pk} apply started (run #{run.pk}).")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


@require_POST
@superuser_required
def full_sync_view(request):
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("sql_neo4j_sync:projection_sync")

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot run full sync.")
        return redirect("sql_neo4j_sync:projection_sync")

    try:
        run = _trigger_workflow("ravioli-sync")
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("sql_neo4j_sync:projection_sync")

    messages.success(request, f"Full sync started (run #{run.pk}) — generate + apply for all labels.")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


@require_POST
@superuser_required
def clear_db_view(request):
    from toto.ravioli.connection import is_enabled

    if not is_enabled():
        messages.error(request, "RAVIOLI_ENABLED is False — cannot connect to Neo4j.")
        return redirect("sql_neo4j_sync:projection_sync")

    if not celery_available():
        messages.error(request, "No Celery worker is running — cannot clear database.")
        return redirect("sql_neo4j_sync:projection_sync")

    try:
        run = _trigger_workflow("ravioli-clear-db")
    except RuntimeError as exc:
        messages.error(request, str(exc))
        return redirect("sql_neo4j_sync:projection_sync")

    messages.warning(request, f"Clear DB started (run #{run.pk}) — deleting all ravioli-owned data.")
    return redirect("workflows:workflow_run_detail", run_id=run.pk)


@require_GET
@superuser_required
def run_projection_stream(request):
    from toto.ravioli.connection import Neo4jClient, is_enabled

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


# ---------------------------------------------------------------------------
# Per-object "Export to graph" button (rendered on app detail pages)
# ---------------------------------------------------------------------------

@require_POST
@login_required
def export_object_to_graph(request, app_label, model_name, object_uuid):
    """Project a single object plus its immediate neighbours into the graph.

    Triggered by the {% export_to_graph_button %} tag on detail pages. Runs
    synchronously (one-hop export is a handful of Cypher statements) and
    redirects back to the page the user came from with a status message.
    """
    from django.apps import apps as django_apps

    from toto.ravioli.connection import Neo4jClient, is_enabled

    from .loader import label_for_model, load_all_configs
    from .projection import ProjectionRunner

    back = request.META.get("HTTP_REFERER") or "/"

    if not is_enabled():
        messages.error(request, "Graph database is disabled (RAVIOLI_ENABLED is False).")
        return redirect(back)

    try:
        model = django_apps.get_model(app_label, model_name)
    except (LookupError, ValueError):
        messages.error(request, "Unknown model for graph export.")
        return redirect(back)

    mapping = label_for_model(model)
    if mapping is None:
        messages.error(
            request,
            f"{model._meta.verbose_name.title()} is not mapped to the graph.",
        )
        return redirect(back)
    label, _uuid_field = mapping

    client = Neo4jClient()
    try:
        runner = ProjectionRunner(client, load_all_configs())
        summary = runner.export_node_one_hop(label, object_uuid)
    except model.DoesNotExist:
        messages.error(request, "That object no longer exists.")
        return redirect(back)
    except Exception as exc:
        messages.error(request, f"Graph export failed: {exc}")
        return redirect(back)
    finally:
        client.close()

    n = summary["neighbours"]
    messages.success(
        request,
        f"Exported to graph as {label} with {n} connection{'' if n == 1 else 's'}.",
    )
    return redirect(back)
