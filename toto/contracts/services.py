from __future__ import annotations

from typing import Any

import yaml
from django.core.exceptions import ValidationError

from .models import (
    SUPPORTED_EDGE_TYPES,
    SUPPORTED_NODE_TYPES,
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


def export_contract_yaml(contract: Contract) -> str:
    """Serialize the contract graph to a YAML snapshot string."""
    node_dicts = []
    for n in contract.nodes.all():
        d: dict[str, Any] = {"id": n.key, "type": n.node_type, "title": n.title}
        if n.description:
            d["description"] = n.description
        if n.is_manual:
            d["manual"] = True
        if n.is_backed:
            d["object"] = {"app": n.object_app, "model": n.object_model, "id": n.object_id}
        if n.metadata:
            d["metadata"] = n.metadata
        node_dicts.append(d)

    edge_dicts = []
    for e in contract.edges.select_related("source", "target").all():
        d: dict[str, Any] = {"from": e.source.key, "to": e.target.key, "type": e.edge_type}
        if e.label:
            d["label"] = e.label
        if e.description:
            d["description"] = e.description
        edge_dicts.append(d)

    doc = {
        "language": "lapis",
        "version": 1,
        "kind": "claims_mesh",
        "name": contract.name,
        "description": contract.description or "",
        "nodes": node_dicts,
        "edges": edge_dicts,
    }
    return yaml.dump(doc, allow_unicode=True, default_flow_style=False, sort_keys=False)


def _validate_import_doc(doc: dict) -> None:
    if doc.get("language") and doc["language"] != "lapis":
        raise ValidationError(f"Unknown language: {doc['language']!r}. Expected 'lapis'.")
    if doc.get("kind") and doc["kind"] != "claims_mesh":
        raise ValidationError(f"Unknown kind: {doc['kind']!r}. Expected 'claims_mesh'.")

    nodes = doc.get("nodes")
    if not isinstance(nodes, list):
        raise ValidationError("'nodes' must be a list.")
    edges = doc.get("edges", [])
    if not isinstance(edges, list):
        raise ValidationError("'edges' must be a list.")

    node_ids: set[str] = set()
    for n in nodes:
        nid = n.get("id")
        if not nid:
            raise ValidationError("Each node must have an 'id' field.")
        if nid in node_ids:
            raise ValidationError(f"Duplicate node id: {nid!r}.")
        node_ids.add(nid)
        ntype = n.get("type")
        if ntype and ntype not in SUPPORTED_NODE_TYPES:
            raise ValidationError(f"Unsupported node type: {ntype!r}.")

    for e in edges:
        etype = e.get("type", "")
        if etype and etype not in SUPPORTED_EDGE_TYPES:
            raise ValidationError(f"Unsupported edge type: {etype!r}.")
        src = e.get("from") or e.get("source")
        tgt = e.get("to") or e.get("target")
        if src and src not in node_ids:
            raise ValidationError(f"Edge source {src!r} not found in nodes.")
        if tgt and tgt not in node_ids:
            raise ValidationError(f"Edge target {tgt!r} not found in nodes.")


def import_contract_yaml(contract: Contract, code: str, replace: bool = True) -> tuple[int, int]:
    """
    Import nodes and edges from a YAML string.
    Returns (nodes_created, edges_created).
    """
    try:
        doc = yaml.safe_load(code)
    except yaml.YAMLError as exc:
        raise ValidationError(f"Invalid YAML: {exc}") from exc

    if not isinstance(doc, dict):
        raise ValidationError("YAML must be a mapping.")

    _validate_import_doc(doc)

    if replace:
        contract.nodes.all().delete()  # cascades edges

    nodes_raw = doc.get("nodes", [])
    edges_raw = doc.get("edges", [])

    for n in nodes_raw:
        obj_ref = n.get("object") or {}
        ContractNode.objects.create(
            contract=contract,
            key=n["id"],
            node_type=n.get("type", "manual"),
            title=n.get("title", n["id"]),
            is_manual=n.get("manual", False),
            description=n.get("description", ""),
            metadata=n.get("metadata") or {},
            object_app=obj_ref.get("app", ""),
            object_model=obj_ref.get("model", ""),
            object_id=str(obj_ref["id"]) if obj_ref.get("id") else "",
        )

    node_map = {n.key: n for n in contract.nodes.all()}
    edges_created = 0
    for e in edges_raw:
        src = node_map.get(e.get("from") or e.get("source", ""))
        tgt = node_map.get(e.get("to") or e.get("target", ""))
        if src and tgt:
            ContractEdge.objects.create(
                contract=contract,
                source=src,
                target=tgt,
                edge_type=e.get("type", "related"),
                label=e.get("label", ""),
                description=e.get("description", ""),
            )
            edges_created += 1

    return len(nodes_raw), edges_created


def sync_contract_code_snapshot(contract: Contract) -> None:
    """Update Contract.code with a fresh YAML snapshot from current graph."""
    contract.code = export_contract_yaml(contract)
    contract.save(update_fields=["code", "updated_at"])
