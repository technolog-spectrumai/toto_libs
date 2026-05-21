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
    HumanTask,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from .serializers import (
    StartRunSerializer,
    SubmitHumanTaskSerializer,
    WorkflowEdgeSerializer,
    WorkflowListSerializer,
    WorkflowNodeSerializer,
    WorkflowRunSerializer,
    WorkflowSerializer,
)
from .services.human_task import submit_human_task
from .services.validator import ValidationError, WorkflowValidator
from .tasks import resume_workflow_run_task, start_workflow_run_task


# ---------------------------------------------------------------------------
#  Workflow CRUD
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
#  Workflow validation
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
#  Nodes
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
#  Edges
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
#  Runs
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
            {
                "error": (
                    "No Celery workers are running. "
                    "Start a Celery worker before executing workflows."
                ),
                "celery_unavailable": True,
            },
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
            "node_runs__node", "node_runs__human_task", "edge_runs__edge"
        ).get(pk=run_id)
    except WorkflowRun.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    return Response(WorkflowRunSerializer(run).data)


# ---------------------------------------------------------------------------
#  Human task submission
# ---------------------------------------------------------------------------

@api_view(["POST"])
def human_task_submit(request, task_id):
    try:
        task = HumanTask.objects.select_related(
            "node_run__node", "node_run__workflow_run__workflow"
        ).get(pk=task_id)
    except HumanTask.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if task.status == HumanTask.SUBMITTED:
        return Response({"detail": "Task already submitted."}, status=status.HTTP_409_CONFLICT)

    ser = SubmitHumanTaskSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    use_celery = celery_available()
    submit_human_task(
        task,
        ser.validated_data["submitted_data"],
        async_lambdas=use_celery,
        resume=not use_celery,
    )
    resume_task_id = None
    if use_celery:
        resume_task_id = resume_workflow_run_task.delay(task.node_run.workflow_run_id).id
    task.refresh_from_db()
    run = task.node_run.workflow_run
    run.refresh_from_db()
    data = WorkflowRunSerializer(run).data
    if resume_task_id:
        data["task_id"] = resume_task_id
    return Response(data)


# ---------------------------------------------------------------------------
#  UI Views
# ---------------------------------------------------------------------------

class WorkflowListUIView(LoginRequiredMixin, ListView):
    model = Workflow
    template_name = "workflows/workflow_list.html"
    context_object_name = "workflows"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        return Workflow.objects.order_by("-created_at")

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
        import json
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
        import json
        context = super().get_context_data(**kwargs)
        run = self.get_object()
        node_runs = list(run.node_runs.select_related("node").order_by("id"))
        edge_runs = list(run.edge_runs.select_related("edge__source", "edge__target").order_by("id"))
        context["node_runs"] = node_runs
        context["edge_runs"] = edge_runs

        pending_tasks = []
        for nr in node_runs:
            if nr.status == WorkflowNodeRun.WAITING:
                try:
                    pending_tasks.append(nr.human_task)
                except HumanTask.DoesNotExist:
                    pass
        context["pending_tasks"] = pending_tasks

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
