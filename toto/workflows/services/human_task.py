from django.utils import timezone

from ..models import HumanTask, WorkflowNodeRun
from ..output import normalize_workflow_output
from .executor import WorkflowExecutor


def apply_output_mapping(submitted_data: dict, config: dict) -> dict:
    mapping = config.get("output_mapping", {})

    data: dict = {}
    for key, spec in (mapping.get("data") or {}).items():
        if not isinstance(spec, dict):
            data[key] = spec
            continue
        if "field" in spec:
            data[key] = submitted_data.get(spec["field"])
        elif "linear_combination" in spec:
            total = sum(
                float(submitted_data.get(term.get("field", ""), 0) or 0)
                * float(term.get("weight", 1))
                for term in spec["linear_combination"]
            )
            data[key] = total

    route_spec = mapping.get("route")
    raw_route: str | None = None

    if route_spec is None:
        pass
    elif isinstance(route_spec, str):
        raw_route = route_spec
    elif isinstance(route_spec, dict):
        field_name = route_spec.get("field")
        field_value = submitted_data.get(field_name) if field_name else None
        field_map = route_spec.get("map")
        if field_map:
            raw_route = field_map.get(str(field_value))
        elif field_value is not None:
            raw_route = str(field_value)

    raw = {"data": data}
    if raw_route is not None:
        raw["route"] = raw_route

    wo = normalize_workflow_output(raw)
    return {"data": wo.data, "routes": wo.routes}


def submit_human_task(task: HumanTask, submitted_data: dict) -> None:
    if task.status == HumanTask.SUBMITTED:
        return

    task.submitted_data = submitted_data
    task.status = HumanTask.SUBMITTED
    task.submitted_at = timezone.now()
    task.save(update_fields=["submitted_data", "status", "submitted_at"])

    node_run: WorkflowNodeRun = task.node_run
    node = node_run.node
    output = apply_output_mapping(submitted_data, node.config or {})

    node_run.output_data = output
    node_run.status = WorkflowNodeRun.COMPLETED
    node_run.completed_at = timezone.now()
    node_run.save(update_fields=["output_data", "status", "completed_at"])

    executor = WorkflowExecutor()
    executor._activate_outgoing_edges(node_run)
    executor.resume(node_run.workflow_run)
