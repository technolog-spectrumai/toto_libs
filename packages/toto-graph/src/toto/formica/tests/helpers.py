"""Shared fixtures: Bento templates, colonies, a synthetic graph sample and an
in-memory GraphOps double so caste/cycle tests run without Neo4j."""

from toto.bento.models import BentoCategory, BentoEdgeType
from toto.formica.colony.cycle import CycleContext, _build_walk_graph
from toto.formica.colony.graph_ops import WriteBudgetExceeded
from toto.formica.models import CasteConfig, Colony


def make_bento_templates():
    """person/org categories + works-at (person → org), knows (person → person)."""
    person = BentoCategory.objects.create(
        name="Person", slug="person", neo4j_label="Person",
        property_schema=[
            {"name": "name", "type": "string", "required": True},
            {"name": "note", "type": "text", "required": False},
        ],
    )
    org = BentoCategory.objects.create(
        name="Org", slug="org", neo4j_label="Org",
        property_schema=[{"name": "name", "type": "string", "required": True}],
    )
    works_at = BentoEdgeType.objects.create(
        name="Works at", slug="works-at", rel_type="WORKS_AT",
    )
    works_at.allowed_sources.set([person])
    works_at.allowed_targets.set([org])
    knows = BentoEdgeType.objects.create(
        name="Knows", slug="knows", rel_type="KNOWS",
    )
    knows.allowed_sources.set([person])
    knows.allowed_targets.set([person])
    return person, org, works_at, knows


def make_colony(**overrides):
    defaults = {"name": "Test Colony", "interval_minutes": 60}
    defaults.update(overrides)
    colony = Colony.objects.create(**defaults)
    for caste_key, ants in (
        ("scout", 5), ("forager", 20), ("nurse", 5),
        ("pruner", 5), ("architect", 5), ("queen", 1),
    ):
        CasteConfig.objects.create(colony=colony, caste_key=caste_key, ant_count=ants)
    return colony


def node(node_id, label="Person", *, props=None):
    base = {"name": node_id.replace("-", " ").title()}
    base.update(props or {})
    return {"id": node_id, "labels": [label], "props": base}


def edge(eid, start, end, *, rel="KNOWS", ph=0.0):
    return {"eid": eid, "type": rel, "start": start, "end": end, "ph": ph}


class FakeGraphOps:
    """In-memory double of GraphOps — records every write for assertions."""

    def __init__(self, *, nodes=None, edges=None, write_budget=5000,
                 cycle_number=1, quarantined=None, incident=None):
        self.nodes = nodes or []
        self.edges = edges or []
        self.write_budget = write_budget
        self.writes_used = 0
        self.cycle_number = cycle_number
        self._quarantined = quarantined or []
        self._incident = incident or {}
        # write records
        self.edge_deposits = []
        self.material_deposits = []
        self.alarms_set = []
        self.alarms_cleared = []
        self.quarantined_ids = []
        self.unquarantined_ids = []
        self.evaporations = []

    # budget -------------------------------------------------------------
    def _spend(self, count):
        if self.writes_used + count > self.write_budget:
            raise WriteBudgetExceeded("budget exhausted")
        self.writes_used += count

    # reads ---------------------------------------------------------------
    def sample_subgraph(self, *, labels, sample_size):
        return self.nodes[:sample_size], self.edges

    def count_marked(self):
        return {"ph_edges": len(self.edges), "ph_mass": 0.0,
                "quarantined": len(self._quarantined)}

    def quarantined_nodes(self, *, max_cycle):
        return [q for q in self._quarantined if q["since"] <= max_cycle]

    def incident_ph(self, node_ids):
        return {i: self._incident.get(i, 0.0) for i in node_ids}

    def hottest_subgraph(self, *, limit=150):
        return {"nodes": [], "edges": []}

    # writes ----------------------------------------------------------------
    def evaporate(self, *, rate, floor, ceiling):
        self.evaporations.append({"rate": rate, "floor": floor, "ceiling": ceiling})
        self._spend(len(self.edges))
        return len(self.edges)

    def decay_node_material(self, *, rate):
        return 0

    def deposit_edge_ph(self, deposits, *, floor, ceiling):
        self._spend(len(deposits))
        self.edge_deposits.extend(deposits)
        return len(deposits)

    def deposit_node_material(self, deposits, *, ceiling):
        self._spend(len(deposits))
        self.material_deposits.extend(deposits)
        return len(deposits)

    def set_alarms(self, node_ids):
        self._spend(len(node_ids))
        self.alarms_set.extend(node_ids)
        return len(node_ids)

    def clear_alarms(self, node_ids):
        self._spend(len(node_ids))
        self.alarms_cleared.extend(node_ids)
        return len(node_ids)

    def quarantine(self, node_ids):
        self._spend(len(node_ids))
        self.quarantined_ids.extend(node_ids)
        return len(node_ids)

    def unquarantine(self, node_ids):
        self._spend(len(node_ids))
        self.unquarantined_ids.extend(node_ids)
        return len(node_ids)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def make_ctx(colony, cycle, *, nodes, edges, graph_ops=None):
    """A CycleContext wired the way execute_cycle's SENSE stage would."""
    ctx = CycleContext(
        colony=colony,
        cycle=cycle,
        params=colony.params(),
        graph_ops=graph_ops or FakeGraphOps(nodes=nodes, edges=edges),
        caste_configs={c.caste_key: c for c in colony.castes.all()},
    )
    ctx.nodes = nodes
    ctx.edges = edges
    ctx.node_by_id = {n["id"]: n for n in nodes}
    ctx.graph = _build_walk_graph(nodes, edges)
    return ctx
