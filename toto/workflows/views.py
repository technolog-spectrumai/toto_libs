from copy import deepcopy
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
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
    Report,
    ReportTemplate,
    WorkflowConnector,
    Workflow,
    WorkflowEdge,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from .serializers import (
    StartRunSerializer,
    SubmitHumanTaskSerializer,
    ReportSerializer,
    ReportTemplateSerializer,
    WorkflowEdgeSerializer,
    WorkflowListSerializer,
    WorkflowNodeSerializer,
    WorkflowRunSerializer,
    WorkflowSerializer,
    WorkflowConnectorSerializer,
)
from .services.human_task import submit_human_task
from .services.reports import render_report
from .services.triggers import (
    TriggerValidationError,
    create_triggered_run,
    get_trigger_node,
    prepare_trigger_submission,
    rerun_initial_values,
    trigger_inputs,
)
from .services.validator import ValidationError, WorkflowValidator
from .tasks import resume_workflow_run_task, start_workflow_run_task


# ---------------------------------------------------------------------------
#  Connector CRUD
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def connector_list(request):
    if request.method == "GET":
        qs = WorkflowConnector.objects.all().order_by("name")
        return Response(WorkflowConnectorSerializer(qs, many=True).data)

    ser = WorkflowConnectorSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    connector = ser.save()
    return Response(WorkflowConnectorSerializer(connector).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT", "PATCH", "DELETE"])
def connector_detail(request, connector_id):
    try:
        connector = WorkflowConnector.objects.get(pk=connector_id)
    except WorkflowConnector.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "GET":
        return Response(WorkflowConnectorSerializer(connector).data)

    if request.method == "DELETE":
        connector.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    partial = request.method == "PATCH"
    ser = WorkflowConnectorSerializer(connector, data=request.data, partial=partial)
    ser.is_valid(raise_exception=True)
    ser.save()
    return Response(WorkflowConnectorSerializer(connector).data)


# ---------------------------------------------------------------------------
#  Reports
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

    if not celery_available():
        return Response(
            {
                "error": (
                    "No Celery workers are running. "
                    "Start a Celery worker before resuming workflows."
                ),
                "celery_unavailable": True,
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    submit_human_task(
        task,
        ser.validated_data["submitted_data"],
        async_lambdas=True,
        resume=False,
    )
    resume_task_id = resume_workflow_run_task.delay(task.node_run.workflow_run_id).id
    task.refresh_from_db()
    run = task.node_run.workflow_run
    run.refresh_from_db()
    data = WorkflowRunSerializer(run).data
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
        return Workflow.objects.prefetch_related("nodes").order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for workflow in context["workflows"]:
            workflow.trigger_node = next(
                (node for node in workflow.nodes.all() if node.node_type == WorkflowNode.TRIGGER),
                None,
            )
        return _decorate(context, self.request)


class ReportListUIView(LoginRequiredMixin, ListView):
    model = Report
    template_name = "workflows/report_list.html"
    context_object_name = "reports"
    login_url = reverse_lazy("core:login")

    def get_queryset(self):
        return Report.objects.select_related("template", "workflow_run").order_by("-created_at")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["templates"] = ReportTemplate.objects.order_by("name")
        return _decorate(context, self.request)


class ReportDetailUIView(LoginRequiredMixin, DetailView):
    model = Report
    template_name = "workflows/report_detail.html"
    context_object_name = "report"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        return get_object_or_404(
            Report.objects.select_related("template", "workflow_run").prefetch_related("pages"),
            pk=self.kwargs["report_id"],
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["render_pages"] = render_report(self.get_object())
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
        context["trigger_node"] = get_trigger_node(workflow)
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


@login_required(login_url=reverse_lazy("core:login"))
@require_POST
def workflow_run_start_ui(request, workflow_id):
    workflow = get_object_or_404(Workflow, pk=workflow_id)
    if get_trigger_node(workflow) is not None:
        return redirect("workflows:workflow_gate", workflow_id=workflow.id)
    try:
        WorkflowValidator().validate(workflow)
    except ValidationError as exc:
        messages.error(
            request,
            "Workflow validation failed: " + "; ".join(exc.errors),
        )
        return redirect("workflows:workflow_detail", workflow_id=workflow.id)

    run = _start_workflow_run(request, workflow, input_data={})
    if run is None:
        return redirect("workflows:workflow_detail", workflow_id=workflow.id)
    return redirect(reverse("workflows:workflow_run_detail", kwargs={"run_id": run.id}))


@login_required(login_url=reverse_lazy("core:login"))
@require_POST
def workflow_run_restart_ui(request, run_id):
    source_run = get_object_or_404(WorkflowRun.objects.select_related("workflow"), pk=run_id)
    if source_run.status != WorkflowRun.COMPLETED:
        messages.error(request, "Only completed workflow runs can be restarted.")
        return redirect(reverse("workflows:workflow_run_detail", kwargs={"run_id": source_run.id}))

    workflow = source_run.workflow
    try:
        WorkflowValidator().validate(workflow)
    except ValidationError as exc:
        messages.error(
            request,
            "Workflow validation failed: " + "; ".join(exc.errors),
        )
        return redirect(reverse("workflows:workflow_run_detail", kwargs={"run_id": source_run.id}))

    run = _start_workflow_run(request, workflow, input_data=deepcopy(source_run.input_data or {}))
    if run is None:
        return redirect(reverse("workflows:workflow_run_detail", kwargs={"run_id": source_run.id}))
    messages.success(request, f"Restarted workflow run #{source_run.id} as run #{run.id}.")
    return redirect(reverse("workflows:workflow_run_detail", kwargs={"run_id": run.id}))


class WorkflowGateUIView(LoginRequiredMixin, DetailView):
    model = Workflow
    template_name = "workflows/workflow_gate.html"
    context_object_name = "workflow"
    login_url = reverse_lazy("core:login")

    def get_object(self, queryset=None):
        return get_object_or_404(Workflow, pk=self.kwargs["workflow_id"])

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return render(request, self.template_name, self._context())

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        workflow = self.object
        try:
            WorkflowValidator().validate(workflow)
            submission = prepare_trigger_submission(workflow, request.POST, request.FILES)
        except ValidationError as exc:
            return render(request, self.template_name, self._context(general_errors=exc.errors), status=422)
        except TriggerValidationError as exc:
            return render(request, self.template_name, self._context(errors=exc.errors), status=422)

        if not celery_available():
            return render(
                request,
                self.template_name,
                self._context(general_errors=[
                    "No Celery workers are running. Start a Celery worker before executing workflows."
                ]),
                status=503,
            )

        run = create_triggered_run(workflow=workflow, submission=submission)
        start_workflow_run_task.delay(run.id)
        messages.success(request, f"Started workflow run #{run.id}.")
        return redirect("workflows:workflow_run_detail", run_id=run.id)

    def _context(self, *, errors=None, general_errors=None):
        workflow = self.object
        trigger_node = get_trigger_node(workflow)
        rerun = self._rerun_source()
        initial = rerun_initial_values(rerun) if rerun else {"parameters": {}, "files": {}}
        context = {
            "workflow": workflow,
            "trigger_node": trigger_node,
            "trigger_inputs": _gate_input_context(trigger_node, initial, self.request.POST if self.request.method == "POST" else None),
            "errors": errors or {},
            "general_errors": general_errors or [],
            "rerun": rerun,
        }
        return _decorate(context, self.request)

    def _rerun_source(self):
        run_id = self.request.GET.get("rerun") or self.request.POST.get("rerun_id")
        if not run_id:
            return None
        return WorkflowRun.objects.filter(pk=run_id, workflow=self.object).first()


@login_required(login_url=reverse_lazy("core:login"))
def workflow_run_legacy_redirect(request, run_id):
    return redirect("workflows:workflow_run_detail", run_id=run_id)


def _start_workflow_run(request, workflow, *, input_data):
    if not celery_available():
        messages.error(
            request,
            "No Celery workers are running. Start a Celery worker before executing workflows.",
        )
        return None
    run = WorkflowRun.objects.create(workflow=workflow, input_data=input_data or {})
    start_workflow_run_task.delay(run.id)
    messages.success(request, f"Started workflow run #{run.id}.")
    return run


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
        failed_node_run = next((node_run for node_run in node_runs if node_run.error), None)

        context["node_runs"] = node_runs
        context["edge_runs"] = edge_runs
        context["reports"] = reports
        context["run_error"] = failed_node_run.display_error if failed_node_run else ""
        context["run_error_node"] = failed_node_run.node if failed_node_run else None
        context.update(_run_user_context(run))

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


def _display_workflow_error(error: str) -> str:
    error = str(error or "")
    if not error:
        return ""
    if error == "kernel_server_timeout" or error == "Kernel error: kernel_server_timeout":
        return "Workflow task timed out."
    if error.startswith("Kernel error: "):
        return "Workflow task error: " + error.removeprefix("Kernel error: ").strip()
    return error


def _gate_input_context(trigger_node, initial: dict, post_data=None) -> list[dict]:
    if trigger_node is None:
        return []
    parameters = initial.get("parameters") or {}
    files = initial.get("files") or {}
    rows = []
    for item in trigger_inputs(trigger_node):
        value = post_data.get(item.key) if post_data is not None else parameters.get(item.key, item.default_value)
        date_value = ""
        time_value = ""
        if item.input_type == "datetime":
            value = parameters.get(item.key, item.default_value) if post_data is None else ""
            if post_data is not None:
                date_value = post_data.get(f"{item.key}__date", "")
                time_value = post_data.get(f"{item.key}__time", "")
            elif value:
                parts = str(value).split("T", 1)
                date_value = parts[0]
                if len(parts) > 1:
                    time_value = parts[1][:5]
        existing_files = files.get(item.key, []) if item.input_type == "file" else []
        existing_file_rows = [
            {"ref": file_ref, "json": json.dumps(file_ref, sort_keys=True)}
            for file_ref in existing_files
            if isinstance(file_ref, dict)
        ]
        rows.append({
            "definition": item,
            "value": "" if value is None else value,
            "date_value": date_value,
            "time_value": time_value,
            "existing_files": existing_files,
            "existing_file_rows": existing_file_rows,
            "accept_attr": item.accepted_file_types,
        })
    return rows


def _run_user_context(run: WorkflowRun) -> dict:
    input_data = run.input_data or {}
    failed_node_run = run.node_runs.exclude(error="").select_related("node").first()
    return {
        "parameters": input_data.get("parameters") or {},
        "files": input_data.get("files") or {},
        "inputs": input_data.get("inputs") or {},
        "lambda_payload": input_data.get("lambda_payload") or {},
        "run_error": _display_workflow_error(failed_node_run.error) if failed_node_run else "",
        "run_error_node": failed_node_run.node if failed_node_run else None,
    }
