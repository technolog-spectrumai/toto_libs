"""Scent sources — pluggable "usefulness" signals feeding the walks.

A scent source is a callable ``(ctx) -> {node_id: weight}`` returning
multiplicative per-node usefulness weights (1.0 = neutral). The cycle engine
averages endpoint weights into each edge's η (heuristic term of the ACO
transition rule). Registering a new signal is one decorated function — the
core never changes.

All sources are best-effort: a source that raises is skipped with a log line,
never failing the cycle.
"""

import logging

logger = logging.getLogger(__name__)

SCENT_SOURCES = []


def register_scent(fn):
    SCENT_SOURCES.append(fn)
    return fn


def compute_node_scent(ctx):
    """Combine all sources into one per-node weight map (multiplicative)."""
    combined = {}
    for source in SCENT_SOURCES:
        try:
            weights = source(ctx) or {}
        except Exception:  # noqa: BLE001 — scent is advisory, never fatal
            logger.exception("formica: scent source %s failed", source.__name__)
            continue
        for node_id, weight in weights.items():
            combined[node_id] = combined.get(node_id, 1.0) * max(float(weight), 0.0)
    return combined


@register_scent
def recency_scent(ctx):
    """Fresh nodes smell interesting: untouched-for-many-cycles decays to 1.0,
    touched-this-cycle boosts to 2.0."""
    current = ctx.cycle.number
    weights = {}
    for node in ctx.nodes:
        touched = node["props"].get("_ph_touched")
        if touched is None:
            continue
        age = max(current - int(touched), 0)
        weights[node["id"]] = 1.0 + max(0.0, 1.0 - age / 10.0)
    return weights


@register_scent
def degree_scent(ctx):
    """Cheap structural importance: degree centrality on the sample, scaled to
    [1.0, 2.0] so isolated nodes stay neutral rather than repellent."""
    graph = ctx.graph
    if graph is None or graph.number_of_nodes() == 0:
        return {}
    import networkx as nx

    centrality = nx.degree_centrality(graph)
    top = max(centrality.values(), default=0.0)
    if top <= 0:
        return {}
    return {node: 1.0 + (c / top) for node, c in centrality.items()}


@register_scent
def search_hits_scent(ctx):
    """Nodes users actually surfaced via graph search smell strongly useful.

    There is no dedicated search log; the celery search path records its
    payload in ``WorkflowRun.output_data`` of the 'ravioli-graph-search'
    workflow (lossy — direct searches leave no trace — which is fine for an
    advisory signal). Weight = 1 + hits, capped at 3.0.
    """
    try:
        from toto.workflows.models import Workflow, WorkflowRun
    except Exception:  # noqa: BLE001 — workflows app may be absent
        return {}

    workflow = Workflow.objects.filter(slug="ravioli-graph-search").first()
    if workflow is None:
        return {}
    sample_ids = {node["id"] for node in ctx.nodes}
    hits = {}
    runs = (
        WorkflowRun.objects.filter(workflow=workflow)
        .order_by("-created_at")
        .values_list("output_data", flat=True)[:50]
    )
    for output in runs:
        for result in (output or {}).get("results", []) or []:
            props = result.get("props") or {}
            node_id = props.get("uid") or props.get("uuid")
            if node_id and node_id in sample_ids:
                hits[node_id] = hits.get(node_id, 0) + 1
    return {node_id: min(1.0 + count, 3.0) for node_id, count in hits.items()}
