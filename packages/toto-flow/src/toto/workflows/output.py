from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowOutput:
    data: dict = field(default_factory=dict)
    routes: list = field(default_factory=list)


def normalize_workflow_output(raw: Any) -> WorkflowOutput:
    """
    Accept either:
      {"data": {...}, "route": "branch"}
      {"data": {...}, "routes": ["a", "b"]}
    and return a normalized WorkflowOutput with .routes always a list.
    """
    if not isinstance(raw, dict):
        return WorkflowOutput()

    data = raw.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if "routes" in raw:
        routes = list(raw["routes"]) if raw["routes"] else []
    elif "route" in raw and raw["route"] is not None:
        routes = [raw["route"]]
    else:
        routes = []

    return WorkflowOutput(data=data, routes=routes)
