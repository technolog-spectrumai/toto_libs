"""Batched weighted random walks — the virtual ants.

Ants are never simulated against Neo4j: the cycle engine loads a bounded
subgraph into NetworkX once, this module walks it in memory, and only the
aggregate edge/node visit counts are flushed back (one UNWIND per batch).
O(sample size) per cycle regardless of ant count, and deterministic under a
fixed seed (``ColonyCycle.rng_seed``) so tests are reproducible.

Transition rule (classic ACO): from node *u*, edge (u,v) is chosen with
probability ∝ τ(u,v)^α · η(u,v)^β, where τ is the ``_ph`` edge attribute and
η an optional per-edge heuristic scent (attribute ``eta``, default 1.0). With
probability ε the ant ignores the trail and picks uniformly (exploration).
"""

import random
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class WalkResult:
    edge_visits: Counter = field(default_factory=Counter)   # (u, v) sorted key → count
    node_visits: Counter = field(default_factory=Counter)   # node id → count
    covisits: Counter = field(default_factory=Counter)      # (u, v) node pair co-visited in one walk
    steps_taken: int = 0
    ants_walked: int = 0


def edge_key(u, v):
    """Canonical undirected edge key (deposits ignore direction)."""
    return (u, v) if str(u) <= str(v) else (v, u)


def _transition_weights(graph, node, *, alpha, beta, ph_floor):
    neighbors = []
    weights = []
    for nbr in graph.neighbors(node):
        data = graph.get_edge_data(node, nbr) or {}
        tau = max(float(data.get("_ph", ph_floor) or ph_floor), ph_floor)
        eta = max(float(data.get("eta", 1.0) or 1.0), 1e-9)
        neighbors.append(nbr)
        weights.append((tau ** alpha) * (eta ** beta))
    return neighbors, weights


def walk(graph, start, *, max_steps, alpha, beta, epsilon, rng, ph_floor=0.01):
    """One ant's walk from ``start``. Returns (visited_nodes, visited_edges)."""
    visited_nodes = [start]
    visited_edges = []
    node = start
    for _step in range(max_steps):
        neighbors, weights = _transition_weights(
            graph, node, alpha=alpha, beta=beta, ph_floor=ph_floor
        )
        if not neighbors:
            break
        if rng.random() < epsilon or sum(weights) <= 0:
            nxt = rng.choice(neighbors)
        else:
            nxt = rng.choices(neighbors, weights=weights, k=1)[0]
        visited_edges.append(edge_key(node, nxt))
        visited_nodes.append(nxt)
        node = nxt
    return visited_nodes, visited_edges


def run_walks(graph, *, n_ants, max_steps, alpha, beta, epsilon, seed,
              start_nodes=None, ph_floor=0.01):
    """Walk ``n_ants`` ants and aggregate their traces into a WalkResult.

    ``start_nodes`` defaults to all graph nodes; starts are sampled with
    replacement so ant count is independent of graph size. Co-visitation is
    counted once per unordered node pair per walk (the termite construction
    signal: nodes that keep appearing on the same trails).
    """
    result = WalkResult()
    nodes = list(start_nodes) if start_nodes is not None else list(graph.nodes)
    if not nodes or n_ants <= 0:
        return result
    rng = random.Random(seed)

    for _ant in range(n_ants):
        start = rng.choice(nodes)
        visited_nodes, visited_edges = walk(
            graph, start, max_steps=max_steps, alpha=alpha, beta=beta,
            epsilon=epsilon, rng=rng, ph_floor=ph_floor,
        )
        result.ants_walked += 1
        result.steps_taken += len(visited_edges)
        for key in visited_edges:
            result.edge_visits[key] += 1
        distinct = sorted(set(visited_nodes), key=str)
        for node in distinct:
            result.node_visits[node] += 1
        # co-visitation: unordered pairs of distinct nodes on this trail,
        # bounded to avoid quadratic blowup on long walks
        for i, u in enumerate(distinct):
            for v in distinct[i + 1 :]:
                result.covisits[edge_key(u, v)] += 1
    return result
