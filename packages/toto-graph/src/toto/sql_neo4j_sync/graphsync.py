"""
Load a NeoJSON document into Neo4j, reusing ravioli's SQL→Neo4j sync engine.

The document is treated as the *desired* graph state and compared against the
current ravioli-owned graph with the very same diff algorithm the projection
planner uses (:func:`toto.ravioli.planner.compute_diff`); the resulting
create/update/delete diff is applied with the same
:class:`~toto.ravioli.planner.ProjectionPlanApplier`.

Two modes:

``merge``    upsert the document's nodes/relationships; never delete.
``replace``  make the **ravioli-owned** graph match the document — anything in the
             managed graph (all configured labels + relationship types) that the
             document doesn't contain is deleted. Labels/relationship types the
             projection config doesn't own are left untouched.

NeoJSON → planner mapping
-------------------------
* ``NeoNode.id``                       → node ``uuid`` (the MERGE key)
* ``NeoNode.labels[0]``                → node ``label``
* ``NeoRelationship`` without ``id``   → a *direct* relationship (FK/M2M style)
* ``NeoRelationship`` with ``id``      → a *junction* relationship; ``id`` is the
                                         ``ravioli_uuid`` carried on the edge
"""

from toto.ravioli.neojson import NeoGraph
from .planner import (
    ProjectionPlanApplier,
    ProjectionPlanner,
    clean_props,
    compute_diff,
    direct_relationship_key,
    junction_relationship_key,
    summarize_diff,
)

MODE_MERGE = "merge"
MODE_REPLACE = "replace"
MODES = (MODE_MERGE, MODE_REPLACE)


def _node_label(node):
    if not node.labels:
        raise ValueError(
            f"NeoJSON node {node.id!r} has no label — ravioli nodes need exactly one label."
        )
    return node.labels[0]


def _node_props(props):
    # Normalise + drop reserved keys so they can't fight the MERGE identity.
    cleaned = clean_props(props)  # strips ravioli_uuid, normalises values
    cleaned.pop("uuid", None)
    return cleaned


def neojson_to_expected(graph: NeoGraph):
    """Map a :class:`NeoGraph` to the planner's ``(expected_nodes, expected_relationships)``
    shape so it can be diffed against the planner's ``actual_*`` output."""
    label_by_id = {}
    expected_nodes = {}
    for node in graph.nodes:
        label = _node_label(node)
        label_by_id[node.id] = label
        expected_nodes.setdefault(label, {})[node.id] = {
            "label": label,
            "uuid": node.id,
            "props": _node_props(node.properties),
        }

    expected_rels = {}
    for rel in graph.relationships:
        from_label = label_by_id.get(rel.start)
        to_label = label_by_id.get(rel.end)
        if from_label is None or to_label is None:
            # Dangling endpoint — neojson.validate() flags these; skip defensively.
            continue
        base = {
            "from_label": from_label,
            "from_uuid": rel.start,
            "relation": rel.rel_type,
            "to_label": to_label,
            "to_uuid": rel.end,
            "props": clean_props(rel.properties),
            "link_key": "",
        }
        if rel.id is not None:
            r = {**base, "kind": "junction", "ravioli_uuid": str(rel.id)}
            r["key"] = junction_relationship_key(r)
        else:
            r = {**base, "kind": "direct"}
            r["key"] = direct_relationship_key(r)
        expected_rels[r["key"]] = r

    return expected_nodes, expected_rels


def diff_neojson(graph, actual_nodes, actual_relationships, *, mode=MODE_MERGE):
    """Pure diff: desired (`graph`) vs current (`actual_*`), honouring the mode.

    For ``replace`` we seed an empty expected bucket for every actual label so the
    planner's per-label delete pass also removes nodes of labels the document omits
    entirely (relationship deletes already span all actual relationships)."""
    expected_nodes, expected_rels = neojson_to_expected(graph)
    if mode == MODE_REPLACE:
        for label in actual_nodes:
            expected_nodes.setdefault(label, {})

    diff, totals = compute_diff(expected_nodes, actual_nodes, expected_rels, actual_relationships)

    if mode == MODE_MERGE:
        diff["nodes"]["delete"] = []
        diff["relationships"]["delete"] = []

    return diff, totals


def load_neojson(client, graph, *, mode=MODE_MERGE, configs=None):
    """Apply a :class:`NeoGraph` to Neo4j and return the change summary.

    ``client`` — a :class:`toto.ravioli.connection.Neo4jClient`.
    Raises ``ValueError`` for an unknown mode.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown load mode {mode!r}; use 'merge' or 'replace'.")

    # All configured labels/links → the full ravioli-owned graph is the comparison base.
    planner = ProjectionPlanner(client, configs=configs)
    actual_nodes = planner.actual_nodes()
    actual_relationships = planner.actual_relationships()

    diff, totals = diff_neojson(graph, actual_nodes, actual_relationships, mode=mode)
    ProjectionPlanApplier(client).apply(diff)

    summary = summarize_diff(diff, totals)
    summary["mode"] = mode
    return summary
