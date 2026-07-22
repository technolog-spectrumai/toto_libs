"""Apply an approved proposal to Neo4j — through Bento, never directly.

Idempotent / resumable: successfully created node uids and applied relationship
temp_ids are recorded in ``IngestProposal.apply_result``. Re-running skips work
already done, so a mid-patch failure can be retried without duplicating nodes.
Only ``approved`` + ``valid`` elements are written; everything is re-validated
against the live Bento templates first.
"""

from django.utils import timezone

from toto.bento import graph_service

from . import validation
from .validation import effective_uid, is_reference


def apply_proposal(proposal, prior_result=None):
    """Apply ``proposal`` (a patch dict). Returns ``(result, errors)``.

    ``prior_result`` lets a previous partial apply resume. Reads/writes graph via
    ``bento.graph_service`` only.
    """
    validation.revalidate(proposal)

    prior = prior_result or {}
    node_uids = dict(prior.get("node_uids", {}))      # temp_id -> uid
    rel_done = set(prior.get("relationships", []))    # applied relationship temp_ids
    errors = []

    for node in proposal.get("nodes", []):
        temp = node["temp_id"]
        if temp in node_uids:
            continue  # resume: already resolved
        if is_reference(node):
            uid = effective_uid(node)
            if uid:
                node_uids[temp] = uid
            continue
        if node.get("approval") != "approved":
            continue
        if (node.get("validation") or {}).get("status") == "error":
            continue
        try:
            created = graph_service.create_node(node["category_slug"], node.get("properties") or {})
            node_uids[temp] = created["uid"]
        except Exception as exc:  # noqa: BLE001 — surface any graph error per-element
            errors.append(f"node {temp} ({node.get('display')}): {exc}")

    for rel in proposal.get("relationships", []):
        temp = rel["temp_id"]
        if temp in rel_done:
            continue
        if rel.get("approval") != "approved":
            continue
        if (rel.get("validation") or {}).get("status") == "error":
            continue
        from_uid = node_uids.get(rel["from"])
        to_uid = node_uids.get(rel["to"])
        if not from_uid or not to_uid:
            errors.append(
                f"rel {temp}: endpoint not applied — approve/merge its nodes first"
            )
            continue
        try:
            graph_service.create_edge(
                rel["edge_type_slug"], from_uid, to_uid, rel.get("properties") or {}
            )
            rel_done.add(temp)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"rel {temp}: {exc}")

    result = {
        "node_uids": node_uids,
        "relationships": sorted(rel_done),
        "errors": errors,
    }
    return result, errors


def run(proposal_model):
    """Apply a persisted :class:`IngestProposal`, updating its status fields."""
    from ..models import IngestProposal

    proposal_model.status = IngestProposal.STATUS_APPLYING
    proposal_model.save(update_fields=["status", "updated_at"])

    result, errors = apply_proposal(proposal_model.proposal, proposal_model.apply_result)

    proposal_model.apply_result = result
    proposal_model.proposal = proposal_model.proposal  # validation mutated in place
    if errors:
        proposal_model.status = IngestProposal.STATUS_FAILED
        proposal_model.error = "\n".join(errors)
    else:
        proposal_model.status = IngestProposal.STATUS_APPLIED
        proposal_model.error = ""
        proposal_model.applied_at = timezone.now()
    proposal_model.save()
    return result, errors
