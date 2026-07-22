"""Validate proposal elements against the live Bento templates.

Bento is the authority: these helpers call ``graph_service.validate_node`` /
``validate_edge`` (non-mutating). Used at build time, on every edit, and again
right before apply (templates may have changed since generation).
"""

from toto.bento import graph_service


def is_reference(node):
    """True when the node references an existing graph node (nothing to create).

    Either it was detected as existing, or the user merged a proposed node into
    an existing one (``merge_into_uid``).
    """
    return node.get("kind") == "existing" or bool(node.get("merge_into_uid"))


def effective_uid(node):
    return node.get("merge_into_uid") or node.get("uid")


def validate_node_element(node):
    """Set and return ``node['validation']``."""
    if is_reference(node):
        errors = []
        if not node.get("category_slug"):
            errors = ["Merged/reference node has no category."]
    elif not node.get("category_slug"):
        errors = ["Select a category before applying."]
    else:
        errors = graph_service.validate_node(node["category_slug"], node.get("properties"))
    node["validation"] = {"status": "error" if errors else "ok", "errors": errors}
    return node["validation"]


def validate_relationship_element(rel, nodes_by_temp):
    """Set and return ``rel['validation']`` (endpoints resolvable + Bento-valid)."""
    src = nodes_by_temp.get(rel.get("from"))
    dst = nodes_by_temp.get(rel.get("to"))
    errors = []
    if src is None or dst is None:
        errors.append("Relationship endpoint is missing.")
    else:
        if not src.get("category_slug") or not dst.get("category_slug"):
            errors.append("An endpoint has no category.")
        else:
            errors += graph_service.validate_edge(
                rel["edge_type_slug"],
                src["category_slug"],
                dst["category_slug"],
                rel.get("properties"),
            )
    rel["validation"] = {"status": "error" if errors else "ok", "errors": errors}
    return rel["validation"]


def revalidate(proposal):
    """Re-run validation across every node and relationship in ``proposal``."""
    nodes_by_temp = {n["temp_id"]: n for n in proposal.get("nodes", [])}
    for node in proposal.get("nodes", []):
        validate_node_element(node)
    for rel in proposal.get("relationships", []):
        validate_relationship_element(rel, nodes_by_temp)
    return proposal


def summarize(proposal):
    """Counts for the review header + apply gating."""
    nodes = proposal.get("nodes", [])
    rels = proposal.get("relationships", [])
    new_nodes = [n for n in nodes if not is_reference(n)]
    existing_nodes = [n for n in nodes if is_reference(n)]
    errors = sum(
        1 for el in (*nodes, *rels)
        if (el.get("validation") or {}).get("status") == "error"
    )
    duplicates = sum(1 for n in nodes if n.get("duplicate_warning"))
    approved = sum(1 for el in (*nodes, *rels) if el.get("approval") == "approved")
    return {
        "new_nodes": len(new_nodes),
        "existing_nodes": len(existing_nodes),
        "relationships": len(rels),
        "errors": errors,
        "duplicates": duplicates,
        "approved": approved,
        "total_changes": len(new_nodes) + len(rels),
    }
