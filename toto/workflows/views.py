import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404
from django.urls import reverse_lazy
from django.views.generic import DetailView, ListView

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from toto.celery_utils import celery_available

try:
    from toto.ui import PageProcessor
    _HAS_PAGE_PROCESSOR = True
except ImportError:
    _HAS_PAGE_PROCESSOR = False


def _decorate(context, request):
    if _HAS_PAGE_PROCESSOR:
        return PageProcessor().decorate(context, request)
    return context


from .models import (
    Report,
    ReportTemplate,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from .serializers import (
    StartRunSerializer,
    ReportSerializer,
    ReportTemplateSerializer,
    WorkflowEdgeSerializer,
    WorkflowListSerializer,
    WorkflowNodeSerializer,
    WorkflowRunSerializer,
    WorkflowSerializer,
)
from .services.reports import render_report
from .services.validator import ValidationError, WorkflowValidator
from .tasks import start_workflow_run_task


# ---------------------------------------------------------------------------
#  Reports API
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def report_template_list(request):
    if request.method == "GET":
        qs = ReportTemplate.objects.all().order_by("name")
        return Response(ReportTemplateSerializer(qs, many=True).data)

    ser = ReportTemplateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    template = ser.save()
    return Response(ReportTemplateSerializer(template).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
def report_template_detail(request, template_id):
    try:
        template = ReportTemplate.objects.get(pk=template_id)
    except ReportTemplate.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(ReportTemplateSerializer(template).data)

    if request.method == "DELETE":
        template.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    partial = request.method == "PATCH"
    ser = ReportTemplateSerializer(template, data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    ser.save()
    return Response(ReportTemplateSerializer(template).data)


@api_view(["GET"])
def report_list(request):
    qs = Report.objects.select_related("template", "workflow_run").prefetch_related("pages").order_by("-created_at")
    return Response(ReportSerializer(qs, many=True).data)


@api_view(["GET"])
def report_detail(request, report_id):
    try:
        report = Report.objects.select_related("template", "workflow_run").prefetch_related("pages").get(pk=report_id)
    except Report.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    return Response(ReportSerializer(report).data)


# ---------------------------------------------------------------------------
#  Workflow CRUD API
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def workflow_list(request):
    if request.method == "GET":
        qs = Workflow.objects.all().order_by("-created_at")
        return Response(WorkflowListSerializer(qs, many=True).data)

    ser = WorkflowSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    workflow = ser.save()
    return Response(WorkflowSerializer(workflow).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
def workflow_detail(request, workflow_id):
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(WorkflowSerializer(workflow).data)

    if request.method == "DELETE":
        workflow.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    partial = request.method == "PATCH"
    ser = WorkflowSerializer(workflow, data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    ser.save()
    return Response(WorkflowSerializer(workflow).data)


# ---------------------------------------------------------------------------
#  Workflow validation API
# ---------------------------------------------------------------------------

@api_view(["POST"])
def validate_workflow(request, workflow_id):
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        WorkflowValidator().validate(workflow)
    except ValidationError as exc:
        return Response({"valid": False, "errors": exc.errors}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    return Response({"valid": True, "errors": []})


# ---------------------------------------------------------------------------
#  Nodes API
# ---------------------------------------------------------------------------

@api_view(["POST"])
def node_create(request, workflow_id):
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    data = {**request.data, "workflow": workflow.id}
    ser = WorkflowNodeSerializer(data=data)
    ser.is_valid(raise_exception=True)
    node = ser.save()
    return Response(WorkflowNodeSerializer(node).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
def node_detail(request, workflow_id, node_id):
    try:
        node = WorkflowNode.objects.get(pk=node_id, workflow_id=workflow_id)
    except WorkflowNode.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(WorkflowNodeSerializer(node).data)

    if request.method == "DELETE":
        node.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    partial = request.method == "PATCH"
    ser = WorkflowNodeSerializer(node, data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    ser.save()
    return Response(WorkflowNodeSerializer(node).data)


# ---------------------------------------------------------------------------
#  Edges API
# ---------------------------------------------------------------------------

@api_view(["POST"])
def edge_create(request, workflow_id):
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    data = {**request.data, "workflow": workflow.id}
    ser = WorkflowEdgeSerializer(data=data)
    ser.is_valid(raise_exception=True)
    edge = ser.save()
    return Response(WorkflowEdgeSerializer(edge).data, status=status.HTTP_201_CREATED)


@api_view(["DELETE"])
def edge_delete(request, workflow_id, edge_id):
    try:
        edge = WorkflowEdge.objects.get(pk=edge_id, workflow_id=workflow_id)
    except WorkflowEdge.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    edge.delete()
    return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
#  Runs API
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def run_list(request, workflow_id):
    try:
        workflow = Workflow.objects.get(pk=workflow_id)
    except Workflow.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        qs = workflow.runs.order_by("-created_at")
        return Response(WorkflowRunSerializer(qs, many=True).data)

    ser = StartRunSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    try:
        WorkflowValidator().validate(workflow)
    except ValidationError as exc:
        return Response(
            {"detail": "Workflow validation failed.", "errors": exc.errors},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if not celery_available():
        return Response(
            {"error": "No Celery workers are running.", "celery_unavailable": True},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    run = WorkflowRun.objects.create(
        workflow=workflow,
        input_data=ser.validated_data.get("input_data") or {},
    )
    task_result = start_workflow_run_task.delay(run.id)
    run.refresh_from_db()
    data = WorkflowRunSerializer(run).data
    data["task_id"] = task_result.id
    return Response(data, status=status.HTTP_201_CREATED)


@api_view(["GET"])
def run_detail(request, run_id):
    try:
        run = WorkflowRun.objects.prefetch_related(
            "node_runs__node", "edge_runs__edge"
        ).get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    return Response(WorkflowRunSerializer(run).data)


@api_view(["POST"])
def cancel_run(request, run_id):
    """Mark a pending or running workflow run as cancelled.

    Idempotent — cancelling an already-terminal run returns 200 with its
    current status so callers don't need to pre-check.
    """
    from django.utils import timezone as _tz
    run = get_object_or_404(WorkflowRun, pk=run_id)

    if run.status in (WorkflowRun.COMPLETED, WorkflowRun.FAILED, "cancelled"):
        return Response({"status": run.status, "detail": "Run already finished."})

    # Mark any in-flight node runs as failed
    WorkflowNodeRun.objects.filter(
        workflow_run=run,
        status__in=[WorkflowNodeRun.PENDING, WorkflowNodeRun.RUNNING],
    ).update(status=WorkflowNodeRun.FAILED, error="Cancelled by user.")

    run.status = "cancelled"
    run.completed_at = _tz.now()
    run.save(update_fields=["status", "completed_at"])
    return Response({"status": "cancelled"})


# ---------------------------------------------------------------------------
#  UI Views
# ---------------------------------------------------------------------------

class WorkflowListUIView(LoginRequiredMixin, ListView):
    model = Workflow
    template_name = "workflows/workflow_list.html"
    context_object_name = "workflows"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        return Workflow.objects.prefetch_related("nodes").order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return _decorate(context, self.request)


class WorkflowDetailUIView(LoginRequiredMixin, DetailView):
    model = Workflow
    template_name = "workflows/workflow_detail.html"
    context_object_name = "workflow"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        return get_object_or_404(Workflow, pk=self.kwargs["workflow_id"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        workflow = self.get_object()
        nodes = list(workflow.nodes.select_related("lambda_function").order_by("id"))
        edges = list(workflow.edges.select_related("source", "target").order_by("id"))
        context["nodes"] = nodes
        context["edges"] = edges
        context["runs"] = workflow.runs.order_by("-created_at")[:20]
        context["graph_nodes_json"] = json.dumps([
            {
                "id": n.id,
                "label": n.label or f"{n.node_type}:{n.id}",
                "node_type": n.node_type,
                "lambda_name": n.lambda_function.function_name if n.lambda_function else "",
                "lambda_code": n.lambda_function.content if n.lambda_function else "",
                "task_name": n.task_name,
            }
            for n in nodes
        ])
        context["graph_edges_json"] = json.dumps([
            {
                "source": e.source_id,
                "target": e.target_id,
                "branch_key": e.branch_key,
                "is_default": e.is_default,
            }
            for e in edges
        ])
        return _decorate(context, self.request)


class WorkflowRunDetailUIView(LoginRequiredMixin, DetailView):
    model = WorkflowRun
    template_name = "workflows/workflow_run_detail.html"
    context_object_name = "run"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        return get_object_or_404(WorkflowRun, pk=self.kwargs["run_id"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run = self.get_object()
        node_runs = list(run.node_runs.select_related("node").order_by("id"))
        edge_runs = list(run.edge_runs.select_related("edge__source", "edge__target").order_by("id"))
        reports = list(
            run.reports.select_related("template", "source_node_run")
            .prefetch_related("pages")
            .order_by("-created_at")
        )
        reports_by_node_run = {}
        for report in reports:
            rendered_pages = render_report(report)
            report.render_block = (
                rendered_pages[0]["blocks"][0]
                if rendered_pages and rendered_pages[0]["blocks"]
                else None
            )
            reports_by_node_run.setdefault(report.source_node_run_id, []).append(report)
        for node_run in node_runs:
            node_run.generated_reports = reports_by_node_run.get(node_run.id, [])
            node_run.display_error = _display_workflow_error(node_run.error)

        failed_node_run = next((nr for nr in node_runs if nr.error), None)

        context["node_runs"] = node_runs
        context["edge_runs"] = edge_runs
        context["reports"] = reports
        context["run_error"] = failed_node_run.display_error if failed_node_run else ""
        context["run_error_node"] = failed_node_run.node if failed_node_run else None

        all_nodes = list(run.workflow.nodes.select_related("lambda_function").order_by("id"))
        all_edges = list(run.workflow.edges.select_related("source", "target").order_by("id"))
        node_run_by_node = {nr.node_id: nr for nr in node_runs}
        edge_run_by_edge = {er.edge_id: er for er in edge_runs}

        context["graph_nodes_json"] = json.dumps([
            {
                "id": n.id,
                "label": n.label or f"{n.node_type}:{n.id}",
                "node_type": n.node_type,
                "status": node_run_by_node[n.id].status if n.id in node_run_by_node else "not_started",
            }
            for n in all_nodes
        ])
        context["graph_edges_json"] = json.dumps([
            {
                "source": e.source_id,
                "target": e.target_id,
                "branch_key": e.branch_key,
                "is_default": e.is_default,
                "activated": edge_run_by_edge[e.id].activated if e.id in edge_run_by_edge else None,
            }
            for e in all_edges
        ])
        return _decorate(context, self.request)


def _display_workflow_error(error: str) -> str:
    error = str(error or "")
    if not error:
        return ""
    if error == "kernel_server_timeout" or error == "Kernel error: kernel_server_timeout":
        return "Workflow task timed out."
    if error.startswith("Kernel error: "):
        return "Workflow task error: " + error.removeprefix("Kernel error: ").strip()
    return error
