"""
ravioli.services.graph_plans — build & apply graph projection plans.

Shared backend for the two review-then-apply flows in ravioli's UI:
  * "Sync all to graph" — the full SQL→Neo4j projection diff.
  * "Prune history"     — delete :HISTORICAL snapshots beyond the N newest per node.

Both produce a ``sql_neo4j_sync.GraphProjectionPlan`` and are applied through the same
``ProjectionPlanApplier``, so the review modal and the apply endpoint are reused for both.
History pruning is always manual — the sync/export path never caps history itself.
"""

from django.conf import settings

HISTORICAL_REL = "HISTORICAL"


def default_keep():
    """Default number of newest snapshots to keep when pruning (per node)."""
    try:
        return max(0, int(getattr(settings, "RAVIOLI_DEFAULT_MAX_HISTORY", 3)))
    except (TypeError, ValueError):
        return 3


# ---------------------------------------------------------------------------
# Sync (full SQL → Neo4j projection)
# ---------------------------------------------------------------------------

def create_sync_plan(client):
    from toto.sql_neo4j_sync.loader import load_all_configs
    from toto.sql_neo4j_sync.planner import create_projection_plan

    return create_projection_plan(client, labels=None, configs=load_all_configs())


# ---------------------------------------------------------------------------
# Prune (delete :HISTORICAL snapshots beyond `keep` newest per canonical node)
# ---------------------------------------------------------------------------

def _projected_labels():
    from toto.sql_neo4j_sync.loader import load_all_configs

    return {
        node["label"]
        for config in load_all_configs()
        for node in config.get("nodes", [])
    }


def _primary_label(labels, projected):
    for label in labels or []:
        if label in projected:
            return label
    return (labels or [""])[0]


def create_prune_plan(client, keep=None):
    """Build (don't apply) a plan that deletes old :HISTORICAL snapshots.

    Keeps the ``keep`` most recent snapshots of each canonical node and marks the
    rest for deletion. Stored as a delete-only ``GraphProjectionPlan`` so the
    normal review modal + apply path handle it unchanged.
    """
    from toto.sql_neo4j_sync.models import GraphProjectionPlan
    from toto.sql_neo4j_sync.planner import summarize_diff

    try:
        keep = default_keep() if keep in (None, "") else max(0, int(keep))
    except (TypeError, ValueError):
        keep = default_keep()

    rows = client.run_cypher(
        f"MATCH (c)-[:{HISTORICAL_REL}]->(h) WHERE h._historical = true "
        "WITH c, h ORDER BY h.archived_at DESC "
        "WITH c, collect(h) AS hs "
        "UNWIND hs[$keep..] AS h "
        "RETURN labels(h) AS h_labels, h.uuid AS h_uuid, "
        "labels(c) AS c_labels, c.uuid AS c_uuid",
        {"keep": keep},
    )

    projected = _projected_labels()
    node_deletes, rel_deletes = [], []
    for row in rows:
        h_label = _primary_label(row["h_labels"], projected)
        c_label = _primary_label(row["c_labels"], projected)
        node_deletes.append({"label": h_label, "uuid": str(row["h_uuid"])})
        rel_deletes.append({
            "kind": "direct",
            "from_label": c_label,
            "from_uuid": str(row["c_uuid"]),
            "relation": HISTORICAL_REL,
            "to_label": h_label,
            "to_uuid": str(row["h_uuid"]),
            "props": {},
        })

    diff = {
        "nodes": {"create": [], "update": [], "delete": node_deletes, "ignored": []},
        "relationships": {
            "create": [], "update": [], "delete": rel_deletes, "ignored": [],
        },
    }
    return GraphProjectionPlan.objects.create(
        status=GraphProjectionPlan.STATUS_READY,
        scope={"kind": "prune", "keep": keep},
        summary=summarize_diff(diff),
        diff=diff,
    )


# ---------------------------------------------------------------------------
# Apply (shared) + payload for the review UI
# ---------------------------------------------------------------------------

def apply_plan(client, plan):
    from toto.sql_neo4j_sync.planner import apply_projection_plan

    apply_projection_plan(client, plan)


def plan_payload(plan, apply_url, detail_url):
    """The JSON the review modal consumes (identical shape for sync and prune)."""
    return {
        "plan_id": plan.id,
        "summary": plan.summary,
        "total_changes": plan.total_changes,
        "diff": plan.diff,
        "apply_url": apply_url,
        "detail_url": detail_url,
    }
