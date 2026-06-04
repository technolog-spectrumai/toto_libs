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


def evaluate_claims_health(contract: Contract) -> dict:
    """
    Walk all backed ContractNode records whose type has a status lifecycle
    (entitlement, schedule, condition, allocation, obligation) and assess the
    live state of each linked object.

    Returns a result dict stored in ``contract.metadata["claims_health"]``:
        {
            "evaluated_at": "<ISO 8601>",
            "overall":       "healthy" | "warning" | "critical" | "unknown",
            "counts":        {"total": N, "healthy": N, "warning": N,
                              "critical": N, "unresolvable": N},
            "details":       [ {node_key, node_type, title, object_label,
                                status, health} … ],
        }
    """
    from django.apps import apps
    from django.utils import timezone

    _HEALTH: dict[str, dict[str, str]] = {
        "entitlement": {
            "active": "healthy", "suspended": "warning",
            "revoked": "critical", "expired": "critical",
        },
        "schedule": {
            "active": "healthy", "completed": "healthy",
            "draft": "warning", "paused": "warning",
            "cancelled": "critical",
        },
        "condition": {
            "satisfied": "healthy", "waived": "healthy",
            "pending": "warning",
            "failed": "critical",
        },
        "allocation": {
            "active": "healthy", "released": "healthy", "consumed": "healthy",
            "draft": "warning",
            "cancelled": "critical",
        },
        "obligation": {
            "fulfilled": "healthy",
            "pending": "warning",
            "overdue": "critical", "defaulted": "critical",
        },
    }
    _TIER = {"critical": 3, "warning": 2, "healthy": 1, "unresolvable": 0}

    details = []
    counts: dict[str, int] = {"total": 0, "healthy": 0, "warning": 0, "critical": 0, "unresolvable": 0}
    worst = "unknown"

    backed = [n for n in contract.nodes.all() if n.is_backed and n.node_type in _HEALTH]

    for node in backed:
        counts["total"] += 1
        entry: dict = {
            "node_key": node.key,
            "node_type": node.node_type,
            "title": node.title,
            "object_label": f"{node.object_app}.{node.object_model}#{node.object_id}",
            "status": "—",
            "health": "unresolvable",
        }
        try:
            Model = apps.get_model(node.object_app, node.object_model)
            obj = Model.objects.get(pk=node.object_id)
            status = str(getattr(obj, "status", "—") or "—")
            entry["status"] = status
            entry["health"] = _HEALTH[node.node_type].get(status, "unresolvable")
            # Pick the best human label available on the object.
            for attr in ("resource_label", "reference", "name"):
                val = getattr(obj, attr, None)
                if val:
                    entry["object_label"] = str(val)
                    break
        except Exception:
            pass

        tier_key = entry["health"]
        counts[tier_key] = counts.get(tier_key, 0) + 1
        if _TIER.get(tier_key, 0) > _TIER.get(worst, -1):
            worst = tier_key

        details.append(entry)

    if not details:
        worst = "unknown"

    return {
        "evaluated_at": timezone.now().isoformat(),
        "overall": worst,
        "counts": counts,
        "details": details,
    }


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


