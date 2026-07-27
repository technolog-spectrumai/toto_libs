"""The colony cycle engine — one epoch, sequential stages, all budget-capped.

    1 SENSE      sample a bounded subgraph, build the walk graph, apply scents
    2 EVAPORATE  batched decay of _ph (edges) and _ph_material (nodes)
    3…N CASTES   registry order: scout → forager → nurse → pruner → architect → queen
    LAST         persist ForagerReport + stats; emit FormicaProposal from
                 collected ops (trusted colonies auto-apply valid ops)

Castes communicate only through the environment (graph markers + ctx/SQL
stats): stigmergy. A cycle that loses the graph mid-run fails cleanly and the
next beat retries fresh.
"""

import logging
from dataclasses import dataclass, field

from django.utils import timezone

from ..models import CasteConfig, ColonyCycle, ForagerReport
from . import scent as scent_mod
from .castes import CASTE_REGISTRY
from .graph_ops import GraphOps, WriteBudgetExceeded

logger = logging.getLogger(__name__)


@dataclass
class CycleContext:
    colony: object
    cycle: object
    params: dict
    graph_ops: object
    nodes: list = field(default_factory=list)       # sampled node dicts
    edges: list = field(default_factory=list)       # sampled edge dicts (eid-keyed)
    graph: object = None                            # undirected nx.Graph for walks
    node_by_id: dict = field(default_factory=dict)
    node_scent: dict = field(default_factory=dict)  # id -> η weight
    walk_result: object = None                      # set by the Forager
    senses: dict = field(default_factory=dict)      # caste_key -> sense payload
    stats: dict = field(default_factory=dict)
    ops: list = field(default_factory=list)         # proposal ops accumulating
    caste_configs: dict = field(default_factory=dict)

    def caste_params(self, caste_key):
        config = self.caste_configs.get(caste_key)
        overrides = config.param_overrides if config else None
        return self.colony.params(overrides)

    def ant_count(self, caste_key):
        config = self.caste_configs.get(caste_key)
        return config.ant_count if config else 0

    def add_op(self, action, params, *, reason, evidence=None):
        """Collect one structural op for the review-gated proposal (capped)."""
        if len(self.ops) >= int(self.params["proposal_batch_max"]):
            self.stats["ops_dropped_over_cap"] = (
                self.stats.get("ops_dropped_over_cap", 0) + 1
            )
            return None
        op = {
            "op_id": f"o{len(self.ops) + 1}",
            "action": action,
            "params": params,
            "reason": reason,
            "evidence": evidence or {},
            "validation": {},
            "approval": "pending",
            "result": {},
        }
        self.ops.append(op)
        return op

    def protected_node(self, node):
        """Hard prune exclusions: protected sets + SQL-projected nodes."""
        if node["id"] in set(self.colony.protected_uids or []):
            return True
        protected_labels = set(self.colony.protected_labels or [])
        if protected_labels and any(l in protected_labels for l in node["labels"]):
            return True
        # Deleting SQL-projected nodes is futile — reconcile recreates them.
        if node["props"].get("uuid"):
            return True
        return False


def _bento_labels():
    from toto.bento.models import BentoCategory

    return {c.neo4j_label: c.slug for c in BentoCategory.objects.all()}


def _build_walk_graph(nodes, edges):
    """Collapse the sampled (multi-di)graph into an undirected walk graph.

    Ants ignore direction and parallel edges; each collapsed edge keeps the
    element ids of all parallels (deposits flush to every one) and the max τ.
    """
    import networkx as nx

    graph = nx.Graph()
    for node in nodes:
        graph.add_node(node["id"], labels=node["labels"])
    for edge in edges:
        u, v = edge["start"], edge["end"]
        if u == v or not graph.has_node(u) or not graph.has_node(v):
            continue
        if graph.has_edge(u, v):
            data = graph[u][v]
            data["eids"].append(edge["eid"])
            data["_ph"] = max(data["_ph"], edge["ph"])
        else:
            graph.add_edge(u, v, eids=[edge["eid"]], _ph=edge["ph"], eta=1.0)
    return graph


def execute_cycle(cycle_id):
    """Run one epoch. Owns every ColonyCycle status transition."""
    cycle = (
        ColonyCycle.objects.select_related("colony")
        .filter(pk=cycle_id)
        .first()
    )
    if cycle is None:
        logger.warning("formica: cycle %s vanished before execution", cycle_id)
        return
    if cycle.status != ColonyCycle.STATUS_PENDING:
        logger.info("formica: cycle %s already %s — skipping", cycle_id, cycle.status)
        return

    colony = cycle.colony
    from toto.ravioli import connection as ravioli_conn

    if colony.paused or not ravioli_conn.is_enabled() or not ravioli_conn.is_alive():
        cycle.status = ColonyCycle.STATUS_ABORTED
        cycle.error = "Colony paused." if colony.paused else "Graph disabled or unreachable."
        cycle.finished_at = timezone.now()
        cycle.save(update_fields=["status", "error", "finished_at"])
        return

    cycle.status = ColonyCycle.STATUS_RUNNING
    cycle.started_at = timezone.now()
    cycle.save(update_fields=["status", "started_at"])

    params = colony.params()
    try:
        with GraphOps(
            write_budget=params["cycle_write_budget"], cycle_number=cycle.number
        ) as graph_ops:
            ctx = CycleContext(
                colony=colony,
                cycle=cycle,
                params=params,
                graph_ops=graph_ops,
                caste_configs={
                    c.caste_key: c
                    for c in CasteConfig.objects.filter(colony=colony, enabled=True)
                },
            )
            _run_stages(ctx)
            _persist_outputs(ctx)
        cycle.status = ColonyCycle.STATUS_COMPLETED
        cycle.error = ""
    except WriteBudgetExceeded as exc:
        # The budget is a safety net, not an error: keep what was written.
        logger.warning("formica: cycle %s hit its write budget: %s", cycle_id, exc)
        cycle.stats = {**(cycle.stats or {}), "budget_exhausted": True}
        cycle.status = ColonyCycle.STATUS_COMPLETED
        cycle.error = ""
    except Exception as exc:  # noqa: BLE001 — the cycle row is the error surface
        logger.exception("formica: cycle %s failed", cycle_id)
        cycle.status = ColonyCycle.STATUS_FAILED
        cycle.error = str(exc)
    finally:
        cycle.finished_at = timezone.now()
        cycle.save()


def _run_stages(ctx):
    graph_ops = ctx.graph_ops
    params = ctx.params

    # ── 1 SENSE ─────────────────────────────────────────────────────────
    labels = _bento_labels()
    ctx.stats["label_map_size"] = len(labels)
    ctx.nodes, ctx.edges = graph_ops.sample_subgraph(
        labels=list(labels.keys()), sample_size=params["walk_sample_size"]
    )
    ctx.node_by_id = {n["id"]: n for n in ctx.nodes}
    ctx.graph = _build_walk_graph(ctx.nodes, ctx.edges)
    ctx.node_scent = scent_mod.compute_node_scent(ctx)
    for u, v, data in ctx.graph.edges(data=True):
        eta_u = ctx.node_scent.get(u, 1.0)
        eta_v = ctx.node_scent.get(v, 1.0)
        data["eta"] = (eta_u + eta_v) / 2.0
    ctx.stats["nodes_sampled"] = len(ctx.nodes)
    ctx.stats["edges_sampled"] = len(ctx.edges)

    # ── 2 EVAPORATE ────────────────────────────────────────────────────
    ctx.stats["evaporated_edges"] = graph_ops.evaporate(
        rate=params["evaporation_rate"],
        floor=params["ph_floor"],
        ceiling=params["ph_max"],
    )
    ctx.stats["decayed_material_nodes"] = graph_ops.decay_node_material(
        rate=params["evaporation_rate"] / 2.0
    )

    # ── 3…N CASTES (registry order) ────────────────────────────────────
    caste_stats = {}
    for caste_key, caste_class in sorted(
        CASTE_REGISTRY.items(), key=lambda kv: kv[1].order
    ):
        config = ctx.caste_configs.get(caste_key)
        if config is None or not config.enabled:
            continue
        caste_stats[caste_key] = caste_class().run(ctx) or {}
    ctx.stats["castes"] = caste_stats
    ctx.stats["writes_used"] = graph_ops.writes_used
    ctx.stats.update(graph_ops.count_marked())


def _persist_outputs(ctx):
    """ForagerReport, cycle stats, and the proposal (if any ops)."""
    from ..services import builder

    report_payload = (ctx.senses.get("forager") or {}).get("report")
    if report_payload:
        ForagerReport.objects.update_or_create(
            cycle=ctx.cycle, defaults={"payload": report_payload}
        )

    proposal = None
    if ctx.ops:
        proposal = builder.build_proposal(ctx)
        ctx.stats["proposal_id"] = proposal.pk
        ctx.stats["proposal_ops"] = proposal.total_ops
        if ctx.colony.trusted:
            from ..tasks import apply_formica_proposal
            from toto.celery_utils import celery_available

            if celery_available():
                apply_formica_proposal.delay(proposal.pk)
            else:
                from ..services.apply import run as apply_run

                apply_run(proposal.pk)
    ctx.cycle.stats = ctx.stats
