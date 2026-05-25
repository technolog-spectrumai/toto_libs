from __future__ import annotations

_TYPE_COLORS = {
    "start":        "#06b6d4",
    "condition":    "#f59e0b",
    "payment":      "#22c55e",
    "record":       "#3b82f6",
    "state":        "#8b5cf6",
    "stop":         "#ef4444",
    "approval":     "#10b981",
    "notification": "#6366f1",
}

_TYPE_SHAPES = {
    "condition": "diamond",
    "stop":      "ellipse",
    "start":     "roundrectangle",
}


def flow_to_cytoscape(tree: dict) -> dict:
    """Convert a Contract Flow dict into Cytoscape.js nodes/edges."""
    steps = tree.get("steps", [])
    if not steps:
        return {"nodes": [], "edges": []}

    nodes = []
    edges = []

    for step in steps:
        step_id = step.get("id", "?")
        step_type = step.get("type", "?")
        title = step.get("title", step_id)
        description = step.get("description", "")

        color = _TYPE_COLORS.get(step_type, "#94a3b8")
        shape = _TYPE_SHAPES.get(step_type, "roundrectangle")

        label = title
        if step_type == "payment":
            from_p = step.get("from", "")
            to_p = step.get("to", "")
            if from_p and to_p:
                label = f"{title}\n({from_p} → {to_p})"

        nodes.append({"data": {
            "id": step_id,
            "label": label,
            "type": step_type,
            "description": description,
            "color": color,
            "shape": shape,
        }})

        if step.get("next"):
            edges.append({"data": {
                "id": f"e-{step_id}-next",
                "source": step_id,
                "target": step["next"],
                "label": "",
            }})
        if step.get("then"):
            edges.append({"data": {
                "id": f"e-{step_id}-then",
                "source": step_id,
                "target": step["then"],
                "label": "yes",
            }})
        if step.get("else"):
            edges.append({"data": {
                "id": f"e-{step_id}-else",
                "source": step_id,
                "target": step["else"],
                "label": "no",
            }})

    return {"nodes": nodes, "edges": edges}


# Legacy alias kept for backward compat during transition
def lapis_to_cytoscape_tree(tree: dict) -> dict:
    return flow_to_cytoscape(tree)
