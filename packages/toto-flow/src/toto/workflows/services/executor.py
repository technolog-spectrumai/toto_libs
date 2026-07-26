"""
WorkflowExecutor — lean DAG runner for chains of lambda functions.

Execution contract
------------------
- Lambda nodes run their LambdaFunction.content in the workflow worker. In
  async_lambdas mode each lambda node is queued as its own Celery task.
  The lambda is expected to print a JSON object to stdout:
      {"data": {...}, "route": "branch_name"}
      {"data": {...}, "routes": ["a", "b"]}
  Any other stdout lines are silently ignored.

- Split nodes read route/routes from their input_data and activate
  matching outgoing edges by edge.branch_key. The default edge fires if
  no branch key matches. Split nodes do not invoke any lambda.

- Join nodes wait until every activated upstream edge has a COMPLETED
  source node_run, then merge all upstream data dicts and continue.

- Report nodes generate a debug/informational Report record.
"""

import json
import logging
from contextlib import redirect_stdout
from io import StringIO

from django.conf import settings
from django.utils import timezone

from ..models import (
    WorkflowEdgeRun,
    WorkflowNode,
    WorkflowNodeRun,
    WorkflowRun,
)
from ..output import normalize_workflow_output

log = logging.getLogger(__name__)


class _AsyncDispatched:
    """Sentinel returned by _run_predefined_task when the task was sent to Celery."""


def _get_kernel_client():
    mock_cls = getattr(settings, "WORKFLOW_KERNEL_CLIENT", None)
    if mock_cls is not None:
        return mock_cls()
    return None


class WorkflowExecutor:
    def __init__(self, *, async_lambdas: bool = False):
        self.async_lambdas = async_lambdas

    def start(self, workflow_run: WorkflowRun) -> None:
        workflow_run.status = WorkflowRun.RUNNING
        workflow_run.started_at = timezone.now()
        workflow_run.save(update_fields=["status", "started_at"])

        start_nodes = list(
            workflow_run.workflow.nodes.filter(incoming_edges__isnull=True)
        )
        for node in start_nodes:
            self._schedule_node(workflow_run, node, workflow_run.input_data or {})

        self._check_completion(workflow_run)

    def _schedule_node(self, workflow_run: WorkflowRun, node: WorkflowNode, input_data: dict) -> None:
        is_async_lambda = self.async_lambdas and node.node_type == WorkflowNode.LAMBDA
        node_run, created = WorkflowNodeRun.objects.get_or_create(
            workflow_run=workflow_run,
            node=node,
            defaults={
                "status": WorkflowNodeRun.PENDING if is_async_lambda else WorkflowNodeRun.RUNNING,
                "input_data": input_data,
                "started_at": None if is_async_lambda else timezone.now(),
            },
        )
        if not created:
            return
        if is_async_lambda:
            self._queue_lambda_node(node_run)
            return
        self._execute_node(node_run)

    def _execute_node(self, node_run: WorkflowNodeRun) -> None:
        node = node_run.node
        try:
            if node.node_type == WorkflowNode.LAMBDA:
                output = self._run_lambda(node_run)
            elif node.node_type == WorkflowNode.SPLIT:
                output = self._run_split(node_run)
            elif node.node_type == WorkflowNode.JOIN:
                output = self._run_join(node_run)
            elif node.node_type == WorkflowNode.REPORT:
                output = self._run_report(node_run)
            elif node.node_type == WorkflowNode.PREDEFINED_TASK:
                output = self._run_predefined_task(node_run)
            else:
                raise ValueError(f"Unknown node_type: {node.node_type!r}")
        except Exception as exc:
            log.exception("Node %s failed: %s", node_run.id, exc)
            node_run.status = WorkflowNodeRun.FAILED
            node_run.error = str(exc)
            node_run.completed_at = timezone.now()
            node_run.save(update_fields=["status", "error", "completed_at"])
            node_run.workflow_run.status = WorkflowRun.FAILED
            node_run.workflow_run.save(update_fields=["status"])
            return

        if isinstance(output, _AsyncDispatched):
            return
        self._complete_node_run(node_run, output)

    def execute_lambda_node_run(self, node_run_id: int) -> dict:
        node_run = WorkflowNodeRun.objects.select_related(
            "node__lambda_function",
            "node__lambda_function__kernel",
            "workflow_run",
        ).get(pk=node_run_id)
        if node_run.node.node_type != WorkflowNode.LAMBDA:
            raise ValueError(f"NodeRun {node_run.id} is not a lambda node.")
        if node_run.status == WorkflowNodeRun.COMPLETED:
            return node_run.output_data or {}
        if node_run.workflow_run.status in (WorkflowRun.FAILED, WorkflowRun.COMPLETED):
            return node_run.output_data or {}

        node_run.status = WorkflowNodeRun.RUNNING
        if node_run.started_at is None:
            node_run.started_at = timezone.now()
        node_run.save(update_fields=["status", "started_at"])

        output = self._run_lambda(node_run)
        self._complete_node_run(node_run, output)
        return node_run.output_data or {}

    def mark_node_run_failed(self, node_run_id: int, error: str) -> None:
        try:
            node_run = WorkflowNodeRun.objects.select_related("workflow_run").get(pk=node_run_id)
        except WorkflowNodeRun.DoesNotExist:
            return
        node_run.status = WorkflowNodeRun.FAILED
        node_run.error = error
        node_run.completed_at = timezone.now()
        node_run.save(update_fields=["status", "error", "completed_at"])
        node_run.workflow_run.status = WorkflowRun.FAILED
        node_run.workflow_run.completed_at = timezone.now()
        node_run.workflow_run.save(update_fields=["status", "completed_at"])

    def _complete_node_run(self, node_run: WorkflowNodeRun, output: dict) -> None:
        node_run.output_data = output if isinstance(output, dict) else {}
        node_run.status = WorkflowNodeRun.COMPLETED
        node_run.completed_at = timezone.now()
        node_run.save(update_fields=["output_data", "status", "completed_at"])

        self._activate_outgoing_edges(node_run)
        self._advance(node_run.workflow_run)

    def _queue_lambda_node(self, node_run: WorkflowNodeRun) -> None:
        from ..tasks import execute_lambda_node_task

        timeout_seconds = self._lambda_task_timeout_seconds(node_run)
        task_result = execute_lambda_node_task.apply_async(
            args=[node_run.id],
            soft_time_limit=timeout_seconds,
            time_limit=timeout_seconds + 5,
        )
        node_run.celery_task_id = task_result.id
        node_run.save(update_fields=["celery_task_id"])

    def _lambda_task_timeout_seconds(self, node_run: WorkflowNodeRun) -> int:
        lambda_fn = node_run.node.lambda_function
        if lambda_fn and lambda_fn.kernel and lambda_fn.kernel.timeout_ms:
            return max(1, int(lambda_fn.kernel.timeout_ms / 1000))
        return int(getattr(settings, "WORKFLOW_LAMBDA_TASK_TIMEOUT_SECONDS", 30))

    def _run_lambda(self, node_run: WorkflowNodeRun) -> dict:
        lambda_fn = node_run.node.lambda_function
        if lambda_fn is None:
            raise ValueError("Lambda node has no lambda_function configured.")

        injected_code = (
            f"import json as _json\n"
            f"_input = _json.loads({json.dumps(json.dumps(node_run.input_data or {}))})\n"
            + lambda_fn.content
        )
        client = _get_kernel_client()
        if client is not None:
            response = client.execute(lambda_fn.id, injected_code)
        else:
            response = self._execute_lambda_content(lambda_fn.content, node_run.input_data or {})

        if "error" in response:
            raise RuntimeError(_format_lambda_error(response["error"]))

        stdout = response.get("stdout", "").strip()
        raw = self._parse_lambda_stdout(stdout)

        wo = normalize_workflow_output(raw)
        return {"data": wo.data, "routes": wo.routes}

    def _execute_lambda_content(self, content: str, input_data: dict) -> dict:
        from toto.core.file_client import FileClient

        stdout = StringIO()
        scope = {
            "__name__": "__workflow_lambda__",
            "_input": input_data,
            "files": FileClient(),
        }
        with redirect_stdout(stdout):
            exec(content, scope, scope)
        return {"stdout": stdout.getvalue()}

    def _parse_lambda_stdout(self, stdout: str) -> dict:
        if not stdout:
            return {}
        for line in reversed(stdout.splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                return json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
        return {}

    def _run_split(self, node_run: WorkflowNodeRun) -> dict:
        return node_run.input_data or {}

    def _run_join(self, node_run: WorkflowNodeRun) -> dict:
        activated_edge_runs = WorkflowEdgeRun.objects.filter(
            workflow_run=node_run.workflow_run,
            edge__target=node_run.node,
            activated=True,
        ).select_related("edge__source")

        merged_data: dict = {}
        merged_routes: list = []
        for er in activated_edge_runs:
            source_run = WorkflowNodeRun.objects.filter(
                workflow_run=node_run.workflow_run,
                node=er.edge.source,
                status=WorkflowNodeRun.COMPLETED,
            ).first()
            if source_run and source_run.output_data:
                merged_data.update(source_run.output_data.get("data", {}))
                merged_routes.extend(source_run.output_data.get("routes", []))

        return {"data": merged_data, "routes": merged_routes}

    def _run_report(self, node_run: WorkflowNodeRun) -> dict:
        from .reports import create_report_from_node_run

        return create_report_from_node_run(node_run)

    def complete_predefined_node_run(self, node_run_id: int, output: dict) -> None:
        node_run = WorkflowNodeRun.objects.select_related("node", "workflow_run").get(pk=node_run_id)
        self._complete_node_run(node_run, output)

    def _run_predefined_task(self, node_run: WorkflowNodeRun) -> dict:
        from toto.workflows.predefined_tasks import get_celery_task, run

        task_name = node_run.node.task_name
        if not task_name:
            raise ValueError("Predefined task node has no task_name set.")

        celery_name = get_celery_task(task_name)
        if celery_name:
            from celery import current_app
            result = current_app.send_task(celery_name, args=[node_run.id])
            node_run.celery_task_id = result.id
            node_run.status = WorkflowNodeRun.RUNNING
            node_run.started_at = timezone.now()
            node_run.save(update_fields=["celery_task_id", "status", "started_at"])
            return _AsyncDispatched()

        return run(task_name, node_run.input_data or {})

    def _activate_outgoing_edges(self, node_run: WorkflowNodeRun) -> None:
        node = node_run.node
        workflow_run = node_run.workflow_run
        outgoing = list(node.outgoing_edges.all())

        if not outgoing:
            return

        if node.node_type == WorkflowNode.SPLIT:
            wo = normalize_workflow_output(node_run.output_data or {})
            routes = set(wo.routes)

            default_edge = None
            matched: list = []
            for edge in outgoing:
                if edge.is_default:
                    default_edge = edge
                elif edge.branch_key and edge.branch_key in routes:
                    matched.append(edge)

            activated_edges = matched if matched else ([default_edge] if default_edge else [])
            activated_ids = {e.id for e in activated_edges}

            now = timezone.now()
            for edge in outgoing:
                is_active = edge.id in activated_ids
                WorkflowEdgeRun.objects.update_or_create(
                    workflow_run=workflow_run,
                    edge=edge,
                    defaults={
                        "activated": is_active,
                        "activated_at": now if is_active else None,
                    },
                )
        else:
            now = timezone.now()
            for edge in outgoing:
                WorkflowEdgeRun.objects.update_or_create(
                    workflow_run=workflow_run,
                    edge=edge,
                    defaults={"activated": True, "activated_at": now},
                )

    def _advance(self, workflow_run: WorkflowRun) -> None:
        workflow_run.refresh_from_db()
        if workflow_run.status in (WorkflowRun.FAILED, WorkflowRun.COMPLETED):
            return

        all_nodes = list(workflow_run.workflow.nodes.prefetch_related("incoming_edges"))

        started_node_ids = set(
            WorkflowNodeRun.objects.filter(workflow_run=workflow_run)
            .values_list("node_id", flat=True)
        )
        completed_node_ids = set(
            WorkflowNodeRun.objects.filter(
                workflow_run=workflow_run, status=WorkflowNodeRun.COMPLETED
            ).values_list("node_id", flat=True)
        )

        edge_run_map: dict[int, bool] = {}
        decided_edge_ids: set[int] = set()
        for er in WorkflowEdgeRun.objects.filter(workflow_run=workflow_run):
            edge_run_map[er.edge_id] = er.activated
            decided_edge_ids.add(er.edge_id)

        for node in all_nodes:
            if node.id in started_node_ids:
                continue

            incoming = list(node.incoming_edges.all())

            if not incoming:
                continue

            incoming_ids = {e.id for e in incoming}
            decided_incoming = incoming_ids & decided_edge_ids

            if node.node_type == WorkflowNode.JOIN:
                if decided_incoming < incoming_ids:
                    continue
                activated_incoming = {eid for eid in incoming_ids if edge_run_map.get(eid)}
                if not activated_incoming:
                    continue
                source_complete = all(
                    e.source_id in completed_node_ids
                    for e in incoming
                    if edge_run_map.get(e.id)
                )
                if not source_complete:
                    continue
                input_data = self._merge_inputs(workflow_run, [
                    e for e in incoming if edge_run_map.get(e.id)
                ], completed_node_ids)
                self._schedule_node(workflow_run, node, input_data)
            else:
                if decided_incoming < incoming_ids:
                    continue
                activated_incoming = {eid for eid in incoming_ids if edge_run_map.get(eid)}
                if activated_incoming < incoming_ids:
                    continue
                if not all(e.source_id in completed_node_ids for e in incoming):
                    continue
                input_data = self._merge_inputs(workflow_run, incoming, completed_node_ids)
                self._schedule_node(workflow_run, node, input_data)

        self._check_completion(workflow_run)

    def _merge_inputs(self, workflow_run: WorkflowRun, activated_edges: list, completed_node_ids: set) -> dict:
        merged_data: dict = {}
        merged_routes: list = []
        seen_nodes: set = set()
        for edge in activated_edges:
            if edge.source_id in seen_nodes:
                continue
            seen_nodes.add(edge.source_id)
            if edge.source_id not in completed_node_ids:
                continue
            source_run = WorkflowNodeRun.objects.filter(
                workflow_run=workflow_run, node_id=edge.source_id
            ).first()
            if source_run and source_run.output_data:
                merged_data.update(source_run.output_data.get("data", {}))
                merged_routes.extend(source_run.output_data.get("routes", []))
        return {"data": merged_data, "routes": merged_routes}

    def _check_completion(self, workflow_run: WorkflowRun) -> None:
        workflow_run.refresh_from_db()
        if workflow_run.status in (WorkflowRun.FAILED, WorkflowRun.COMPLETED):
            return

        node_runs = WorkflowNodeRun.objects.filter(workflow_run=workflow_run)
        if node_runs.filter(status__in=[WorkflowNodeRun.RUNNING, WorkflowNodeRun.PENDING]).exists():
            return
        workflow_run.status = WorkflowRun.COMPLETED
        workflow_run.completed_at = timezone.now()
        workflow_run.save(update_fields=["status", "completed_at"])


def _format_lambda_error(error: str) -> str:
    error = str(error)
    if error == "kernel_server_timeout":
        return "Workflow task timed out."
    return f"Workflow task error: {error}"
