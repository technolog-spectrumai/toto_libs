from __future__ import annotations

from typing import Any

from .models import (
    NODE_TYPE_SHAPE,
    Contract,
    ContractEdge,
    ContractNode,
)


def create_node(
    contract: Contract,
    key: str,
    node_type: str,
    title: str,
    object=None,
    is_manual: bool = False,
    metadata: dict | None = None,
    description: str = "",
    position_x: float | None = None,
    position_y: float | None = None,
) -> ContractNode:
    """Create or update a contract node. Idempotent by (contract, key)."""
    defaults: dict[str, Any] = {
        "node_type": node_type,
        "title": title,
        "is_manual": is_manual,
        "description": description,
        "metadata": metadata or {},
    }
    if position_x is not None:
        defaults["position_x"] = position_x
    if position_y is not None:
        defaults["position_y"] = position_y

    if object is not None:
        defaults["object_app"] = object._meta.app_label
        defaults["object_model"] = object._meta.model_name
        defaults["object_id"] = str(object.pk)

    node, _ = ContractNode.objects.update_or_create(
        contract=contract,
        key=key,
        defaults=defaults,
    )
    return node


def create_manual_node(
    contract: Contract,
    key: str,
    title: str,
    node_type: str = "manual",
    description: str = "",
    metadata: dict | None = None,
    position_x: float | None = None,
    position_y: float | None = None,
) -> ContractNode:
    """Convenience wrapper for manually-authored explanatory nodes."""
    return create_node(
        contract=contract,
        key=key,
        node_type=node_type,
        title=title,
        is_manual=True,
        description=description,
        metadata=metadata,
        position_x=position_x,
        position_y=position_y,
    )


def create_edge(
    contract: Contract,
    source_key: str,
    target_key: str,
    edge_type: str,
    label: str = "",
    description: str = "",
    metadata: dict | None = None,
) -> ContractEdge:
    """Create or update a contract edge. Idempotent by (contract, source, target, edge_type, label)."""
    source = ContractNode.objects.get(contract=contract, key=source_key)
    target = ContractNode.objects.get(contract=contract, key=target_key)

    edge, _ = ContractEdge.objects.update_or_create(
        contract=contract,
        source=source,
        target=target,
        edge_type=edge_type,
        label=label,
        defaults={
            "description": description,
            "metadata": metadata or {},
        },
    )
    return edge


def _node_url(node: ContractNode) -> str:
    if not node.is_backed:
        return ""
    try:
        from django.urls import reverse, NoReverseMatch
        app = node.object_app
        model = node.object_model
        oid = node.object_id
        candidates = [
            f"{app}:{model}_detail",
            f"{app}:{model.replace('ledger', '')}_detail",
        ]
        for name in candidates:
            try:
                return reverse(name, args=[oid])
            except (NoReverseMatch, Exception):
                pass
    except Exception:
        pass
    return ""


def _node_status(node: ContractNode) -> str:
    if not node.is_backed:
        return ""
    try:
        obj = node.get_object()
        if obj and hasattr(obj, "status"):
            return str(obj.status)
    except Exception:
        pass
    return ""


def contract_to_cytoscape(contract: Contract) -> dict:
    """Serialize a contract graph to Cytoscape elements. No color fields returned."""
    nodes = []
    for n in contract.nodes.all():
        nodes.append({"data": {
            "id": n.key,
            "label": n.title,
            "type": n.node_type,
            "shape": NODE_TYPE_SHAPE.get(n.node_type, "ellipse"),
            "is_manual": n.is_manual,
            "status": _node_status(n),
            "model": f"{n.object_app}.{n.object_model}" if n.is_backed else "",
            "pk": n.object_id if n.is_backed else "",
            "url": _node_url(n),
            "description": n.description,
            "details": n.metadata,
        }})

    edges = []
    for e in contract.edges.select_related("source", "target").all():
        edges.append({"data": {
            "source": e.source.key,
            "target": e.target.key,
            "label": e.label or e.edge_type,
            "type": e.edge_type,
            "description": e.description,
        }})

    return {"nodes": nodes, "edges": edges}


