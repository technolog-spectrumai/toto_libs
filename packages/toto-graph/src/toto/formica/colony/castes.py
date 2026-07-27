"""Caste registry — "many types of ants" is a registry, not a fixed set.

Each caste implements ``sense(ctx) → decide(ctx) → act(ctx)`` and
self-registers via the ``@register_caste`` decorator; adding a behaviour means
adding one class. Castes never call each other — they communicate only through
the environment (graph markings + the cycle context/SQL stats): stigmergy.

``ctx`` is a :class:`~toto.formica.colony.cycle.CycleContext`. Conventions:

- ``sense`` reads the environment into ``ctx.senses[caste_key]`` (no writes).
- ``decide`` turns senses into intents (pure; testable without a graph).
- ``act`` performs bounded writes: direct marker writes through
  ``ctx.graph_ops`` (budget-enforced) and/or structural ops appended via
  ``ctx.add_op(...)`` for the review-gated proposal.
"""

CASTE_REGISTRY = {}


def register_caste(cls):
    if not getattr(cls, "caste_key", ""):
        raise ValueError("Castes must define a caste_key.")
    existing = CASTE_REGISTRY.get(cls.caste_key)
    if existing is not None and existing is not cls:
        raise ValueError(f"Caste '{cls.caste_key}' is already registered by {existing.__name__}.")
    CASTE_REGISTRY[cls.caste_key] = cls
    return cls


def get_caste(caste_key):
    cls = CASTE_REGISTRY.get(caste_key)
    return cls() if cls else None


def caste_choices():
    return [
        {"key": key, "label": cls.label or key, "description": cls.description}
        for key, cls in sorted(CASTE_REGISTRY.items(), key=lambda kv: kv[1].order)
    ]


class BaseCaste:
    caste_key = ""
    label = ""
    description = ""
    order = 100  # execution order within a cycle (lower runs first)

    def run(self, ctx):
        """The full sense → decide → act pipeline for one cycle."""
        senses = self.sense(ctx)
        ctx.senses[self.caste_key] = senses
        intents = self.decide(ctx, senses)
        return self.act(ctx, intents)

    # Subclasses override the three stages. Defaults are inert so a partially
    # configured colony never crashes a cycle.
    def sense(self, ctx):
        return {}

    def decide(self, ctx, senses):
        return []

    def act(self, ctx, intents):
        return {}


def _category_by_label():
    from toto.bento.models import BentoCategory

    return {c.neo4j_label: c for c in BentoCategory.objects.all()}


@register_caste
class ScoutCaste(BaseCaste):
    """Coverage: seed pheromone into stale regions, raise alarms on orphans."""

    caste_key = "scout"
    label = "Scout"
    description = "Samples the stale frontier, seeds pheromone, flags anomalies."
    order = 10

    def sense(self, ctx):
        # The sample is ordered stalest-first (by _ph_touched), so the frontier
        # is simply its head; orphans have no edge in the sampled neighbourhood.
        frontier_size = max(ctx.ant_count(self.caste_key), 1) * 5
        frontier = [n["id"] for n in ctx.nodes[:frontier_size]]
        orphans = [
            n["id"] for n in ctx.nodes
            if ctx.graph.degree(n["id"]) == 0 and not n["props"].get("uuid")
        ]
        return {"frontier": frontier, "orphans": orphans}

    def decide(self, ctx, senses):
        params = ctx.caste_params(self.caste_key)
        floor = params["ph_floor"]
        seed_value = floor + 0.1 * params["deposit_strength"]
        deposits = []
        for node_id in senses["frontier"]:
            for _u, _v, data in ctx.graph.edges(node_id, data=True):
                if data["_ph"] <= floor:
                    for eid in data["eids"]:
                        deposits.append({"eid": eid, "ph": seed_value})
                    data["_ph"] = seed_value
        return {"deposits": deposits, "alarms": senses["orphans"]}

    def act(self, ctx, intents):
        params = ctx.caste_params(self.caste_key)
        seeded = 0
        if intents["deposits"]:
            seeded = ctx.graph_ops.deposit_edge_ph(
                intents["deposits"], floor=params["ph_floor"], ceiling=params["ph_max"]
            )
        alarmed = 0
        if intents["alarms"]:
            alarmed = ctx.graph_ops.set_alarms(intents["alarms"])
        return {"edges_seeded": seeded, "alarms_set": alarmed,
                "orphans": len(intents["alarms"])}


@register_caste
class ForagerCaste(BaseCaste):
    """The ACO core: weighted walks reinforce useful trails, harvest stats."""

    caste_key = "forager"
    label = "Forager"
    description = "Walks the trails (τ^α·η^β), reinforces pheromone, harvests stats."
    order = 20

    def sense(self, ctx):
        return {}

    def decide(self, ctx, senses):
        from .walker import run_walks

        params = ctx.caste_params(self.caste_key)
        return run_walks(
            ctx.graph,
            n_ants=ctx.ant_count(self.caste_key),
            max_steps=params["max_steps_per_ant"],
            alpha=params["alpha"],
            beta=params["beta"],
            epsilon=params["exploration_bias"],
            seed=ctx.cycle.rng_seed,
            ph_floor=params["ph_floor"],
        )

    def act(self, ctx, walk_result):
        from . import pheromone

        params = ctx.caste_params(self.caste_key)
        ctx.walk_result = walk_result

        # Flush aggregate edge deposits: one new τ per collapsed edge, written
        # to every parallel elementId behind it.
        deposits = []
        for (u, v), visits in walk_result.edge_visits.items():
            data = ctx.graph.get_edge_data(u, v)
            if not data:
                continue
            new_tau = pheromone.deposit(
                data["_ph"], float(visits), params["deposit_strength"],
                params["ph_floor"], params["ph_max"],
            )
            data["_ph"] = new_tau  # downstream castes see fresh trails
            for eid in data["eids"]:
                deposits.append({"eid": eid, "ph": new_tau})
        deposited = 0
        if deposits:
            deposited = ctx.graph_ops.deposit_edge_ph(
                deposits, floor=params["ph_floor"], ceiling=params["ph_max"]
            )

        # Termite construction material accrues where trails pass.
        material = [
            {"id": node_id, "material": float(count)}
            for node_id, count in walk_result.node_visits.items()
        ]
        if material:
            ctx.graph_ops.deposit_node_material(material, ceiling=params["ph_max"])

        report = self._harvest_report(ctx)
        ctx.senses[self.caste_key] = {
            "report": report,
            "covisits": dict(walk_result.covisits),
        }
        return {
            "ants": walk_result.ants_walked,
            "steps": walk_result.steps_taken,
            "edges_reinforced": deposited,
            "nodes_visited": len(walk_result.node_visits),
        }

    def _harvest_report(self, ctx):
        """Aggregates only: per-label counts, hottest nodes, degree buckets,
        per-label property fill rates."""
        label_counts = {}
        fill_totals = {}
        categories = _category_by_label()
        for node in ctx.nodes:
            for node_label in node["labels"]:
                label_counts[node_label] = label_counts.get(node_label, 0) + 1
                category = categories.get(node_label)
                if category is None:
                    continue
                declared = [f.get("name") for f in (category.property_schema or [])]
                if not declared:
                    continue
                bucket = fill_totals.setdefault(node_label, {"filled": 0, "slots": 0})
                bucket["slots"] += len(declared)
                bucket["filled"] += sum(
                    1 for name in declared if node["props"].get(name) not in (None, "")
                )

        incident = {}
        for u, v, data in ctx.graph.edges(data=True):
            incident[u] = incident.get(u, 0.0) + data["_ph"]
            incident[v] = incident.get(v, 0.0) + data["_ph"]
        hottest = sorted(incident.items(), key=lambda kv: -kv[1])[:10]

        degree_buckets = {"0": 0, "1-2": 0, "3-9": 0, "10+": 0}
        for node in ctx.nodes:
            degree = ctx.graph.degree(node["id"])
            if degree == 0:
                degree_buckets["0"] += 1
            elif degree <= 2:
                degree_buckets["1-2"] += 1
            elif degree <= 9:
                degree_buckets["3-9"] += 1
            else:
                degree_buckets["10+"] += 1

        def _display(node_id):
            props = ctx.node_by_id.get(node_id, {}).get("props", {})
            return props.get("name") or props.get("title") or node_id

        return {
            "label_counts": label_counts,
            "hottest": [
                {"uid": node_id, "display": _display(node_id), "ph": round(ph, 3)}
                for node_id, ph in hottest
            ],
            "degree_buckets": degree_buckets,
            "fill_rates": {
                label: round(b["filled"] / b["slots"], 3) if b["slots"] else 1.0
                for label, b in fill_totals.items()
            },
        }


@register_caste
class NurseCaste(BaseCaste):
    """Repair: template violations and SQL↔graph drift become update ops."""

    caste_key = "nurse"
    label = "Nurse"
    description = "Validates nodes against Bento templates; repairs drift."
    order = 30

    def sense(self, ctx):
        from toto.bento import graph_service

        categories = _category_by_label()
        check_cap = max(ctx.ant_count(self.caste_key), 1) * 10
        # Alarmed nodes first (Scout/previous cycles flagged them).
        ordered = sorted(
            ctx.nodes, key=lambda n: 0 if n["props"].get("_ph_alarm") else 1
        )
        violations, healthy_alarmed = [], []
        for node in ordered[:check_cap]:
            category = next(
                (categories[l] for l in node["labels"] if l in categories), None
            )
            if category is None:
                continue
            errors = graph_service.validate_node(category.slug, node["props"])
            if errors:
                violations.append(
                    {"node": node, "category": category, "errors": errors}
                )
            elif node["props"].get("_ph_alarm"):
                healthy_alarmed.append(node["id"])
        drift = self._sense_drift(ctx)
        return {
            "violations": violations,
            "healthy_alarmed": healthy_alarmed,
            "drift": drift,
            "checked": min(len(ordered), check_cap),
        }

    def _sense_drift(self, ctx):
        """SQL-projected nodes whose graph props drifted from their SQL row."""
        from toto.ravioli.graph_export import GraphExporter
        from toto.sql_neo4j_sync.loader import build_label_by_model

        by_label = {}
        for model, (label, uuid_field) in build_label_by_model().items():
            by_label[label] = (model, uuid_field)

        drift_cap = max(ctx.ant_count(self.caste_key), 1)
        drifted = []
        exporter = None
        for node in ctx.nodes:
            if len(drifted) >= drift_cap:
                break
            uuid = node["props"].get("uuid")
            if not uuid:
                continue
            hit = next(
                ((l, by_label[l]) for l in node["labels"] if l in by_label), None
            )
            if hit is None:
                continue
            label, (model, uuid_field) = hit
            obj = model.objects.filter(**{uuid_field: uuid}).first()
            if obj is None:
                continue  # graph-side orphan; reconcile's problem, not a repair
            if exporter is None:
                exporter = GraphExporter(ctx.graph_ops.client)
            try:
                _root, _nodes, _edges, node_status, _es = exporter.diff_slice(label, obj)
            except Exception:  # noqa: BLE001 — drift sensing is best-effort
                continue
            if node_status.get((label, str(uuid))) == "changed":
                drifted.append({"label": label, "uuid": str(uuid), "id": node["id"]})
        return drifted

    def decide(self, ctx, senses):
        repairs = []
        for violation in senses["violations"]:
            node, category = violation["node"], violation["category"]
            declared = {f.get("name"): f for f in (category.property_schema or [])}
            props = {
                name: node["props"][name]
                for name in declared
                if node["props"].get(name) not in (None, "")
            }
            display = node["props"].get("name") or node["props"].get("title") or node["id"]
            fills = {}
            for name, spec in declared.items():
                if spec.get("required") and name not in props:
                    fills[name] = display if spec.get("type") in ("string", "text") else None
            fills = {k: v for k, v in fills.items() if v is not None}
            if not fills:
                continue  # nothing we can safely auto-fill; alarm stays up
            repairs.append({
                "node": node,
                "props": {**props, **fills},
                "errors": violation["errors"],
                "fills": fills,
            })
        return {"repairs": repairs, "drift": senses["drift"],
                "clear": senses["healthy_alarmed"],
                "alarm": [v["node"]["id"] for v in senses["violations"]]}

    def act(self, ctx, intents):
        for repair in intents["repairs"]:
            node = repair["node"]
            ctx.add_op(
                "update_node",
                {"uid": node["id"], "properties": repair["props"]},
                reason=f"nurse: template violation — {'; '.join(repair['errors'])}",
                evidence={"fills": repair["fills"], "labels": node["labels"]},
            )
        for drifted in intents["drift"]:
            ctx.add_op(
                "update_node",
                {"resync_slice": {"label": drifted["label"], "uuid": drifted["uuid"]}},
                reason="nurse: SQL↔graph drift — re-sync the projected slice",
                evidence={"uid": drifted["id"]},
            )
        if intents["alarm"]:
            ctx.graph_ops.set_alarms(intents["alarm"])
        if intents["clear"]:
            ctx.graph_ops.clear_alarms(intents["clear"])
        return {
            "checked": ctx.senses[self.caste_key]["checked"],
            "violations": len(intents["alarm"]),
            "repairs_proposed": len(intents["repairs"]),
            "drift_resyncs": len(intents["drift"]),
        }


@register_caste
class PrunerCaste(BaseCaste):
    """Two-phase prune: quarantine marker first, delete op after grace."""

    caste_key = "pruner"
    label = "Pruner"
    description = "Quarantines unused nodes; proposes deletes after grace."
    order = 40

    def sense(self, ctx):
        params = ctx.caste_params(self.caste_key)
        incident = {}
        for u, v, data in ctx.graph.edges(data=True):
            incident[u] = incident.get(u, 0.0) + data["_ph"]
            incident[v] = incident.get(v, 0.0) + data["_ph"]

        cold, recovered = [], []
        for node in ctx.nodes:
            ph = incident.get(node["id"], 0.0)
            quarantined = node["props"].get("_formica_quarantine") is not None
            if ph < params["prune_threshold"]:
                if not quarantined and not ctx.protected_node(node):
                    cold.append({"id": node["id"], "ph": ph})
            elif quarantined:
                recovered.append(node["id"])

        grace_cutoff = ctx.cycle.number - params["prune_grace_cycles"]
        overdue = ctx.graph_ops.quarantined_nodes(max_cycle=grace_cutoff)
        return {"cold": cold, "recovered": recovered, "overdue": overdue}

    def decide(self, ctx, senses):
        params = ctx.caste_params(self.caste_key)
        cap = int(params["max_prunes_per_cycle"])

        to_quarantine = [c["id"] for c in senses["cold"][:cap]]

        # Phase 2 — only nodes still cold after the grace window, re-probed
        # against the LIVE graph (they may not be in this cycle's sample).
        overdue = [
            o for o in senses["overdue"]
            if not ctx.protected_node(
                {"id": o["id"], "labels": o["labels"], "props": o["props"]}
            )
        ]
        live_ph = ctx.graph_ops.incident_ph([o["id"] for o in overdue])
        deletes, late_recoveries = [], []
        for node in overdue:
            if live_ph.get(node["id"], 0.0) < params["prune_threshold"]:
                deletes.append(node)
            else:
                late_recoveries.append(node["id"])
        return {
            "quarantine": to_quarantine,
            "unquarantine": senses["recovered"] + late_recoveries,
            "deletes": deletes[:cap],
        }

    def act(self, ctx, intents):
        quarantined = 0
        if intents["quarantine"]:
            quarantined = ctx.graph_ops.quarantine(intents["quarantine"])
        if intents["unquarantine"]:
            ctx.graph_ops.unquarantine(intents["unquarantine"])
        if intents["deletes"]:
            ctx.add_op(
                "delete_nodes",
                {"uids": [d["id"] for d in intents["deletes"]]},
                reason=(
                    f"pruner: pheromone below threshold for "
                    f"{ctx.params['prune_grace_cycles']}+ cycles"
                ),
                evidence={
                    "nodes": [
                        {"uid": d["id"], "quarantined_since": d["since"]}
                        for d in intents["deletes"]
                    ]
                },
            )
        return {
            "quarantined": quarantined,
            "recovered": len(intents["unquarantine"]),
            "deletes_proposed": len(intents["deletes"]),
        }


@register_caste
class ArchitectCaste(BaseCaste):
    """Termite construction: propose edges where material accumulated."""

    caste_key = "architect"
    label = "Termite architect"
    description = "Builds connections where trails co-visit and material piles up."
    order = 50

    def sense(self, ctx):
        forager = ctx.senses.get("forager") or {}
        covisits = forager.get("covisits") or {}
        walk = ctx.walk_result
        material = {}
        for node in ctx.nodes:
            base = float(node["props"].get("_ph_material") or 0.0)
            fresh = float(walk.node_visits.get(node["id"], 0)) if walk else 0.0
            material[node["id"]] = base + fresh
        return {"covisits": covisits, "material": material}

    def decide(self, ctx, senses):
        from toto.bento import graph_service

        params = ctx.caste_params(self.caste_key)
        categories = _category_by_label()
        cap = max(ctx.ant_count(self.caste_key), 1)

        def slug_of(node_id):
            node = ctx.node_by_id.get(node_id)
            if not node:
                return None
            return next(
                (categories[l].slug for l in node["labels"] if l in categories), None
            )

        candidates = []
        ranked = sorted(senses["covisits"].items(), key=lambda kv: -kv[1])
        for (u, v), covisits in ranked:
            if len(candidates) >= cap:
                break
            if covisits < 2 or ctx.graph.has_edge(u, v):
                continue
            if (
                senses["material"].get(u, 0.0) < params["build_threshold"]
                or senses["material"].get(v, 0.0) < params["build_threshold"]
            ):
                continue
            src_slug, tgt_slug = slug_of(u), slug_of(v)
            if not src_slug or not tgt_slug:
                continue
            edge_types = graph_service.edge_types_between(src_slug, tgt_slug)
            from_id, to_id = u, v
            if not edge_types:
                edge_types = graph_service.edge_types_between(tgt_slug, src_slug)
                from_id, to_id = v, u
            if not edge_types:
                continue  # the templates forbid every direction — never invent
            similarity = self._similarity(ctx, from_id, to_id)
            if similarity is not None and similarity < params["similarity_min"]:
                continue
            candidates.append({
                "edge_type_slug": edge_types[0].slug,
                "from": from_id,
                "to": to_id,
                "covisits": covisits,
                "similarity": similarity,
            })
        return candidates

    def _similarity(self, ctx, u, v):
        """Optional vector gate — best-effort, None when embeddings are off."""
        try:
            from toto.ravioli.vector_search import (
                VectorSearchUnavailable,
                retrieve_vector_results,
            )
        except Exception:  # noqa: BLE001
            return None

        def display(node_id):
            props = ctx.node_by_id.get(node_id, {}).get("props", {})
            return props.get("name") or props.get("title") or ""

        query = display(u)
        target = display(v)
        if not query or not target:
            return None
        try:
            results = retrieve_vector_results(query, top_k=10)
        except VectorSearchUnavailable:
            return None
        except Exception:  # noqa: BLE001
            return None
        for result in results:
            props = result.get("props") or {}
            if (props.get("name") or props.get("title")) == target:
                return result.get("score")
        return None

    def act(self, ctx, candidates):
        from toto.bento import graph_service

        built = 0
        for candidate in candidates:
            # The sample can miss existing edges — probe the live graph before
            # proposing (create_edge is a CREATE, duplicates are forever).
            if graph_service.edge_exists(
                candidate["edge_type_slug"], candidate["from"], candidate["to"]
            ):
                continue
            reason = f"architect: co-visitation {candidate['covisits']}"
            if candidate["similarity"] is not None:
                reason += f" + sim {candidate['similarity']:.2f}"
            ctx.add_op(
                "create_edge",
                {
                    "edge_type_slug": candidate["edge_type_slug"],
                    "from_uid": candidate["from"],
                    "to_uid": candidate["to"],
                    "properties": {},
                },
                reason=reason,
                evidence={
                    "co_visits": candidate["covisits"],
                    "similarity": candidate["similarity"],
                },
            )
            built += 1
        return {"builds_proposed": built}


@register_caste
class QueenCaste(BaseCaste):
    """Regulation: read colony health, reallocate ants across castes."""

    caste_key = "queen"
    label = "Queen"
    description = "Reallocates ant counts based on colony health each cycle."
    order = 90

    #: caste_key → stats path used as its "need" signal
    NEED_SIGNALS = {
        "scout": lambda s: s.get("castes", {}).get("scout", {}).get("orphans", 0),
        "nurse": lambda s: s.get("castes", {}).get("nurse", {}).get("violations", 0),
        "pruner": lambda s: s.get("quarantined", 0),
        "architect": lambda s: s.get("castes", {}).get("architect", {}).get("builds_proposed", 0),
        "forager": lambda s: 1,  # always mildly needed — the baseline
    }

    def sense(self, ctx):
        return {"stats": ctx.stats}

    def decide(self, ctx, senses):
        needs = {
            key: max(int(signal(senses["stats"])), 0)
            for key, signal in self.NEED_SIGNALS.items()
            if key in ctx.caste_configs
        }
        if not needs:
            return None
        neediest = max(needs, key=lambda k: needs[k])
        if needs[neediest] <= 0:
            return None
        counts = {
            key: ctx.caste_configs[key].ant_count for key in needs
        }
        donor = max(
            (k for k in counts if k != neediest),
            key=lambda k: counts[k],
            default=None,
        )
        if donor is None or counts[donor] <= 1:
            return None
        total = sum(counts.values())
        step = max(1, total // 10)
        step = min(step, counts[donor] - 1)  # a caste never drops below 1 ant
        return {"from": donor, "to": neediest, "step": step, "needs": needs}

    def act(self, ctx, intent):
        if not intent:
            return {"reallocated": 0}
        donor = ctx.caste_configs[intent["from"]]
        receiver = ctx.caste_configs[intent["to"]]
        donor.ant_count -= intent["step"]
        receiver.ant_count += intent["step"]
        donor.save(update_fields=["ant_count", "updated_at"])
        receiver.save(update_fields=["ant_count", "updated_at"])
        return {
            "reallocated": intent["step"],
            "from": intent["from"],
            "to": intent["to"],
            "needs": intent["needs"],
        }
