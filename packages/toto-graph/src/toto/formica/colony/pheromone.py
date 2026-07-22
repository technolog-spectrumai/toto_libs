"""Pheromone arithmetic — pure functions, no I/O.

The graph-side application of these rules (batched cypher) lives in
``graph_ops``; keeping the math here makes the clamps and decay unit-testable
without a graph.
"""


def clamp(value, floor, ceiling):
    return max(floor, min(ceiling, value))


def evaporate(value, rate, floor, ceiling):
    """One multiplicative decay step: τ ← clamp(τ·(1−rate))."""
    return clamp(value * (1.0 - rate), floor, ceiling)


def deposit(value, amount, strength, floor, ceiling):
    """One reinforcement step: τ ← clamp(τ + strength·amount)."""
    return clamp(value + strength * amount, floor, ceiling)


def deposits_from_visits(edge_visits, *, strength, floor, ceiling, current=None):
    """Aggregate walk visit counts into per-edge pheromone values.

    ``edge_visits`` maps edge key → visit count; ``current`` maps edge key →
    existing τ (missing = floor). Returns edge key → new clamped τ. This is
    the single batched flush of one cycle's foraging (aggregate-flow ACO) —
    O(edges visited), independent of ant count.
    """
    current = current or {}
    out = {}
    for key, visits in edge_visits.items():
        tau = current.get(key, floor)
        out[key] = deposit(tau, float(visits), strength, floor, ceiling)
    return out


def material_from_covisits(covisit_counts, *, similarity_bonus=None, floor=0.0,
                           ceiling=10000.0, current=None):
    """Termite construction deposit: m ← m + co-visits (+ similarity bonus)."""
    current = current or {}
    similarity_bonus = similarity_bonus or {}
    out = {}
    for key, visits in covisit_counts.items():
        m = current.get(key, 0.0) + float(visits) + float(similarity_bonus.get(key, 0.0))
        out[key] = clamp(m, floor, ceiling)
    return out
