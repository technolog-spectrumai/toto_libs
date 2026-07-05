"""Colony meta-parameters — single source of truth.

``PARAM_SPECS`` drives model validation *and* the control-panel form, so
adding a tunable is a one-line change here. Values live in
``Colony.meta_params`` (JSON); anything unset falls back to ``default``.
"""

PARAM_SPECS = {
    # ── pheromone dynamics ──────────────────────────────────────────────
    "evaporation_rate": {
        "type": float, "default": 0.10, "min": 0.0, "max": 0.5,
        "group": "pheromone",
        "help": "Fraction of edge pheromone that evaporates each cycle.",
    },
    "deposit_strength": {
        "type": float, "default": 1.0, "min": 0.0, "max": 10.0,
        "group": "pheromone",
        "help": "Multiplier on every pheromone deposit (Forager and Scout).",
    },
    "ph_floor": {
        "type": float, "default": 0.01, "min": 0.0, "max": 1.0,
        "group": "pheromone",
        "help": "Pheromone never evaporates below this floor.",
    },
    "ph_max": {
        "type": float, "default": 100.0, "min": 1.0, "max": 10000.0,
        "group": "pheromone",
        "help": "Pheromone never accumulates above this ceiling.",
    },
    # ── foraging walks ─────────────────────────────────────────────────
    "alpha": {
        "type": float, "default": 1.0, "min": 0.0, "max": 5.0,
        "group": "foraging",
        "help": "Pheromone exponent (τ^α) in the walk transition weight.",
    },
    "beta": {
        "type": float, "default": 1.0, "min": 0.0, "max": 5.0,
        "group": "foraging",
        "help": "Heuristic-scent exponent (η^β) in the walk transition weight.",
    },
    "exploration_bias": {
        "type": float, "default": 0.15, "min": 0.0, "max": 1.0,
        "group": "foraging",
        "help": "ε-greedy chance of ignoring the trail and picking a random edge.",
    },
    "max_steps_per_ant": {
        "type": int, "default": 8, "min": 1, "max": 50,
        "group": "foraging",
        "help": "Maximum steps a single ant walks per cycle.",
    },
    "walk_sample_size": {
        "type": int, "default": 500, "min": 50, "max": 5000,
        "group": "foraging",
        "help": "Maximum nodes sampled into the in-worker subgraph per cycle.",
    },
    # ── pruning ────────────────────────────────────────────────────────
    "prune_threshold": {
        "type": float, "default": 0.05, "min": 0.0, "max": 1.0,
        "group": "pruning",
        "help": "Nodes whose incident pheromone stays below this are candidates.",
    },
    "prune_grace_cycles": {
        "type": int, "default": 3, "min": 1, "max": 20,
        "group": "pruning",
        "help": "Cycles a node stays quarantined before a delete is proposed.",
    },
    "max_prunes_per_cycle": {
        "type": int, "default": 50, "min": 0, "max": 1000,
        "group": "pruning",
        "help": "Hard cap on delete ops proposed in one cycle.",
    },
    # ── building (termite) ─────────────────────────────────────────────
    "build_threshold": {
        "type": float, "default": 5.0, "min": 0.5, "max": 100.0,
        "group": "building",
        "help": "Construction material a node pair needs before an edge is proposed.",
    },
    "similarity_min": {
        "type": float, "default": 0.75, "min": 0.0, "max": 1.0,
        "group": "building",
        "help": "Minimum vector similarity for similarity-driven build proposals.",
    },
    # ── budgets ────────────────────────────────────────────────────────
    "cycle_write_budget": {
        "type": int, "default": 5000, "min": 100, "max": 100000,
        "group": "budgets",
        "help": "Hard cap on direct metadata writes (pheromone/markers) per cycle.",
    },
    "proposal_batch_max": {
        "type": int, "default": 200, "min": 1, "max": 2000,
        "group": "budgets",
        "help": "Hard cap on ops emitted into a single proposal.",
    },
}

PARAM_GROUPS = ("pheromone", "foraging", "pruning", "building", "budgets")


def defaults():
    return {name: spec["default"] for name, spec in PARAM_SPECS.items()}


def validate_params(params):
    """Return a list of error strings (empty = valid). Unknown keys are errors."""
    if not isinstance(params, dict):
        return ["meta_params must be a JSON object."]
    errors = []
    for name, value in params.items():
        spec = PARAM_SPECS.get(name)
        if spec is None:
            errors.append(f"Unknown parameter '{name}'.")
            continue
        expected = spec["type"]
        # bools are ints in Python — reject them explicitly for numeric params.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"'{name}' must be a number.")
            continue
        if expected is int and not isinstance(value, int):
            errors.append(f"'{name}' must be an integer.")
            continue
        if not (spec["min"] <= value <= spec["max"]):
            errors.append(
                f"'{name}' must be between {spec['min']} and {spec['max']}."
            )
    return errors


def resolve(params, overrides=None):
    """Merge defaults ← colony params ← caste overrides into a flat dict."""
    merged = defaults()
    merged.update({k: v for k, v in (params or {}).items() if k in PARAM_SPECS})
    if overrides:
        merged.update({k: v for k, v in overrides.items() if k in PARAM_SPECS})
    return merged
