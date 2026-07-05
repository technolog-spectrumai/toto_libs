"""Apply a FormicaProposal — through bento/ravioli, never raw Neo4j.

Mirrors ``ingestor.services.apply``: every op is re-validated against the
live templates first, applied in its own try/except, and its outcome recorded
in ``apply_result`` so a retry after partial failure is idempotent and
resumable. Ops are uid-based mutations of existing elements.

Verified gotcha this module owns: ``graph_service.update_node`` re-enforces
*required* schema props on every call, and shoves undeclared keys into the
``extra`` blob — so update payloads are built by merging the op's props over
the node's **declared** current props only (never markers/undeclared).
"""

import logging

from django.utils import timezone

from toto.bento import graph_service

from ..models import FormicaProposal

logger = logging.getLogger(__name__)

ACTIONS = ("create_edge", "update_node", "delete_nodes")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def _category_of(node):
    from toto.bento.models import BentoCategory

    slug = node.get("category_slug")
    return BentoCategory.objects.filter(slug=slug).first() if slug else None


def _declared_merge(node, category, new_props):
    """Op props merged over the node's declared current props (only those)."""
    declared = {f.get("name") for f in (category.property_schema or [])}
    current = {
        k: v for k, v in (node.get("properties") or {}).items()
        if k in declared and v not in (None, "")
    }
    return {**current, **{k: v for k, v in new_props.items() if k in declared}}


def validate_op(op):
    """Return a list of error strings for one op (empty = appliable)."""
    action = op.get("action")
    params = op.get("params") or {}
    if action not in ACTIONS:
        return [f"Unknown action '{action}'."]
    try:
        if action == "create_edge":
            return _validate_create_edge(params)
        if action == "update_node":
            return _validate_update_node(params)
        return _validate_delete_nodes(params)
    except graph_service.GraphUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — a broken op must not kill the batch
        return [f"Validation failed: {exc}"]


def _validate_create_edge(params):
    slug = params.get("edge_type_slug")
    if not slug or not params.get("from_uid") or not params.get("to_uid"):
        return ["create_edge requires edge_type_slug, from_uid and to_uid."]
    try:
        source = graph_service.get_node(params["from_uid"])
        target = graph_service.get_node(params["to_uid"])
    except graph_service.NotFound as exc:
        return [str(exc)]
    return graph_service.validate_edge(
        slug,
        source.get("category_slug"),
        target.get("category_slug"),
        params.get("properties") or {},
    )


def _validate_update_node(params):
    resync = params.get("resync_slice")
    if resync is not None:
        if not resync.get("label") or not resync.get("uuid"):
            return ["resync_slice requires label and uuid."]
        model_info = _resolve_slice_model(resync["label"])
        if model_info is None:
            return [f"No SQL model projects label '{resync['label']}'."]
        model, uuid_field = model_info
        if not model.objects.filter(**{uuid_field: resync["uuid"]}).exists():
            return [f"SQL row {resync['label']}/{resync['uuid']} no longer exists."]
        return []
    if not params.get("uid"):
        return ["update_node requires a uid."]
    try:
        node = graph_service.get_node(params["uid"])
    except graph_service.NotFound as exc:
        return [str(exc)]
    category = _category_of(node)
    if category is None:
        return [f"Node {params['uid']} has no bento category."]
    merged = _declared_merge(node, category, params.get("properties") or {})
    return graph_service.validate_node(category.slug, merged)


def _validate_delete_nodes(params):
    uids = params.get("uids")
    if not isinstance(uids, list) or not uids:
        return ["delete_nodes requires a non-empty uids list."]
    return []


def revalidate(ops):
    """Set ``op['validation']`` on every op; returns the ops list."""
    for op in ops:
        errors = validate_op(op)
        op["validation"] = {"status": "error" if errors else "ok", "errors": errors}
    return ops


def summarize(ops):
    by_action = {}
    for op in ops:
        by_action[op["action"]] = by_action.get(op["action"], 0) + 1
    return {
        "total_ops": len(ops),
        "by_action": by_action,
        "errors": sum(
            1 for op in ops if (op.get("validation") or {}).get("status") == "error"
        ),
        "approved": sum(1 for op in ops if op.get("approval") == "approved"),
    }


def approve_all_valid(proposal):
    """Approve every op that passes validation (never error ops)."""
    ops = proposal.op_list
    revalidate(ops)
    for op in ops:
        if (op.get("validation") or {}).get("status") != "error":
            op["approval"] = "approved"
    proposal.ops = {"ops": ops}
    proposal.summary = summarize(ops)
    proposal.save(update_fields=["ops", "summary", "updated_at"])


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _resolve_slice_model(label):
    from toto.sql_neo4j_sync.loader import build_label_by_model

    for model, (model_label, uuid_field) in build_label_by_model().items():
        if model_label == label:
            return model, uuid_field
    return None


def _apply_create_edge(params):
    slug = params["edge_type_slug"]
    from_uid, to_uid = params["from_uid"], params["to_uid"]
    if graph_service.edge_exists(slug, from_uid, to_uid):
        return {"status": "done", "detail": "edge already exists — skipped"}
    edge = graph_service.create_edge(slug, from_uid, to_uid, params.get("properties") or {})
    return {"status": "done", "detail": "edge created", "created_uid": edge.get("id")}


def _apply_update_node(params):
    resync = params.get("resync_slice")
    if resync is not None:
        from toto.ravioli.graph_export import GraphExporter

        model, uuid_field = _resolve_slice_model(resync["label"])
        obj = model.objects.get(**{uuid_field: resync["uuid"]})
        exporter = None
        try:
            from toto.ravioli.connection import Neo4jClient

            client = Neo4jClient()
            exporter = GraphExporter(client)
            outcome = exporter.apply(resync["label"], obj)
        finally:
            if exporter is not None:
                client.close()
        return {"status": "done", "detail": f"slice re-synced: {outcome}"}
    node = graph_service.get_node(params["uid"])
    category = _category_of(node)
    merged = _declared_merge(node, category, params.get("properties") or {})
    graph_service.update_node(params["uid"], merged)
    return {"status": "done", "detail": "node updated"}


def _apply_delete_nodes(params):
    count = graph_service.delete_nodes(params["uids"])
    return {"status": "done", "detail": f"{count} node(s) deleted"}


_APPLIERS = {
    "create_edge": _apply_create_edge,
    "update_node": _apply_update_node,
    "delete_nodes": _apply_delete_nodes,
}


def apply_ops(ops, prior_result=None):
    """Apply approved + valid ops; skip already-done (resume). Returns
    ``(result, errors)`` with the ingestor ``apply_result`` contract."""
    prior = prior_result or {}
    done = dict(prior.get("done", {}))
    errors = []

    revalidate(ops)
    for op in ops:
        op_id = op["op_id"]
        if op_id in done:
            op["result"] = done[op_id]
            continue  # resume: already applied
        if op.get("approval") != "approved":
            continue
        if (op.get("validation") or {}).get("status") == "error":
            continue
        try:
            outcome = _APPLIERS[op["action"]](op.get("params") or {})
            done[op_id] = outcome
            op["result"] = outcome
        except Exception as exc:  # noqa: BLE001 — per-op isolation
            message = f"{op_id} ({op['action']}): {exc}"
            errors.append(message)
            op["result"] = {"status": "error", "detail": str(exc)}

    result = {"done": done, "errors": errors}
    return result, errors


def run(proposal_id, user_id=None):
    """Apply a persisted proposal, updating its status fields."""
    proposal = FormicaProposal.objects.filter(pk=proposal_id).first()
    if proposal is None:
        logger.warning("formica: proposal %s vanished before apply", proposal_id)
        return None, ["proposal not found"]
    if proposal.status in (FormicaProposal.STATUS_APPLIED,):
        return proposal.apply_result, []

    proposal.status = FormicaProposal.STATUS_APPLYING
    proposal.save(update_fields=["status", "updated_at"])

    ops = proposal.op_list
    result, errors = apply_ops(ops, proposal.apply_result)

    proposal.ops = {"ops": ops}
    proposal.apply_result = result
    proposal.summary = summarize(ops)
    if errors:
        proposal.status = FormicaProposal.STATUS_FAILED
        proposal.error = "\n".join(errors)
    else:
        proposal.status = FormicaProposal.STATUS_APPLIED
        proposal.error = ""
        proposal.applied_at = timezone.now()
        if user_id:
            proposal.applied_by_id = user_id
    proposal.save()
    return result, errors
