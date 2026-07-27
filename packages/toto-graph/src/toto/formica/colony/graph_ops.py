"""The colony's hands — the ONLY formica module touching ``Neo4jClient``.

Direct writes are *metadata only* (pheromone + markers), always batched
(``UNWIND`` — one round-trip per batch) and budget-capped. Anything that
changes graph structure goes through proposals → ``bento.graph_service``.

Addressing follows bento's conventions (verified): relationships by
``elementId(r)``, nodes by their ``uid`` OR ``uuid`` property. The legacy
numeric-id helpers on ``Neo4jClient`` are deliberately not used.

Markers are underscore-prefixed and excluded from ravioli's content checksum
(``graph_export.RESERVED_PREFIXES``). They are ephemeral by design: SQL link
projection recreates configured relationships (dropping edge markers), and
that is fine — pheromone re-accrues.
"""

import logging

logger = logging.getLogger(__name__)

BATCH_SIZE = 1000

# Nodes/relationships formica must never traverse, mark, or prune.
HISTORICAL_FILTER = "coalesce(n._historical, false) = false"


class WriteBudgetExceeded(RuntimeError):
    pass


class GraphOps:
    """One instance per cycle; owns a single client and the write budget."""

    def __init__(self, *, write_budget, cycle_number):
        from toto.ravioli.connection import Neo4jClient

        self.client = Neo4jClient()
        self.write_budget = int(write_budget)
        self.writes_used = 0
        self.cycle_number = int(cycle_number)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── budget ──────────────────────────────────────────────────────────

    def _spend(self, count):
        if self.writes_used + count > self.write_budget:
            raise WriteBudgetExceeded(
                f"cycle write budget exhausted "
                f"({self.writes_used}+{count} > {self.write_budget})"
            )
        self.writes_used += count

    def _batches(self, rows):
        for i in range(0, len(rows), BATCH_SIZE):
            yield rows[i : i + BATCH_SIZE]

    # ── reads ───────────────────────────────────────────────────────────

    def sample_subgraph(self, *, labels, sample_size):
        """Bounded random-ish sample: nodes (uid-keyed) + their mutual edges.

        Returns ``(nodes, edges)`` shaped for the walker:
        nodes ``{"id": uid_or_uuid, "labels": [...], "props": {...}}``;
        edges ``{"eid": elementId, "type": rel_type, "start": id, "end": id,
        "ph": float}``. Historical snapshots are excluded on both ends;
        nodes lacking both uid and uuid are unaddressable and skipped.
        """
        node_records = self.client.run_cypher(
            f"""
            MATCH (n)
            WHERE any(l IN labels(n) WHERE l IN $labels)
              AND {HISTORICAL_FILTER}
              AND coalesce(n.uid, n.uuid) IS NOT NULL
            RETURN coalesce(n.uid, n.uuid) AS id, labels(n) AS labels,
                   properties(n) AS props
            ORDER BY coalesce(n._ph_touched, -1) ASC, id
            LIMIT $limit
            """,
            {"labels": list(labels), "limit": int(sample_size)},
        )
        nodes = [
            {"id": r["id"], "labels": list(r["labels"]), "props": dict(r["props"])}
            for r in node_records
        ]
        ids = [n["id"] for n in nodes]
        if not ids:
            return nodes, []
        edge_records = self.client.run_cypher(
            """
            MATCH (a)-[r]->(b)
            WHERE coalesce(a.uid, a.uuid) IN $ids
              AND coalesce(b.uid, b.uuid) IN $ids
              AND NOT type(r) = '_HISTORICAL'
            RETURN elementId(r) AS eid, type(r) AS type,
                   coalesce(a.uid, a.uuid) AS start,
                   coalesce(b.uid, b.uuid) AS end,
                   coalesce(r._ph, 0.0) AS ph
            """,
            {"ids": ids},
        )
        edges = [
            {
                "eid": r["eid"], "type": r["type"],
                "start": r["start"], "end": r["end"], "ph": float(r["ph"]),
            }
            for r in edge_records
        ]
        return nodes, edges

    def count_marked(self):
        """Colony-health counters for the Queen/UI (single round-trip)."""
        records = self.client.run_cypher(
            """
            OPTIONAL MATCH ()-[r]->() WHERE r._ph IS NOT NULL
            WITH count(r) AS ph_edges, coalesce(sum(r._ph), 0.0) AS ph_mass
            OPTIONAL MATCH (q) WHERE q._formica_quarantine IS NOT NULL
            RETURN ph_edges, ph_mass, count(q) AS quarantined
            """
        )
        rec = records[0]
        return {
            "ph_edges": rec["ph_edges"],
            "ph_mass": round(float(rec["ph_mass"]), 4),
            "quarantined": rec["quarantined"],
        }

    def quarantined_nodes(self, *, max_cycle):
        """Nodes quarantined at or before ``max_cycle`` (grace elapsed)."""
        records = self.client.run_cypher(
            f"""
            MATCH (n)
            WHERE n._formica_quarantine IS NOT NULL
              AND n._formica_quarantine <= $max_cycle
              AND {HISTORICAL_FILTER}
            RETURN coalesce(n.uid, n.uuid) AS id, labels(n) AS labels,
                   n.uuid AS uuid, n._formica_quarantine AS since,
                   properties(n) AS props
            """,
            {"max_cycle": int(max_cycle)},
        )
        return [
            {
                "id": r["id"], "labels": list(r["labels"]), "uuid": r["uuid"],
                "since": r["since"], "props": dict(r["props"]),
            }
            for r in records
        ]

    def hottest_subgraph(self, *, limit=150):
        """Cytoscape payload of the top-τ edges + their endpoints (map UI)."""
        records = self.client.run_cypher(
            f"""
            MATCH (a)-[r]->(b)
            WHERE r._ph IS NOT NULL AND NOT type(r) = '_HISTORICAL'
              AND coalesce(a._historical, false) = false
              AND coalesce(b._historical, false) = false
            RETURN elementId(r) AS eid, type(r) AS type, r._ph AS ph,
                   coalesce(a.uid, a.uuid) AS a_id, labels(a) AS a_labels,
                   coalesce(a.name, a.title, coalesce(a.uid, a.uuid)) AS a_display,
                   a._formica_quarantine AS a_quarantine,
                   coalesce(b.uid, b.uuid) AS b_id, labels(b) AS b_labels,
                   coalesce(b.name, b.title, coalesce(b.uid, b.uuid)) AS b_display,
                   b._formica_quarantine AS b_quarantine
            ORDER BY r._ph DESC
            LIMIT $limit
            """,
            {"limit": int(limit)},
        )
        nodes, edges = {}, []
        for rec in records:
            for prefix in ("a", "b"):
                node_id = rec[f"{prefix}_id"]
                if node_id and node_id not in nodes:
                    nodes[node_id] = {
                        "id": node_id,
                        "label": str(rec[f"{prefix}_display"]),
                        "category": (list(rec[f"{prefix}_labels"]) or [""])[0],
                        "quarantined": rec[f"{prefix}_quarantine"] is not None,
                    }
            if rec["a_id"] and rec["b_id"]:
                edges.append({
                    "id": rec["eid"],
                    "source": rec["a_id"],
                    "target": rec["b_id"],
                    "type": rec["type"],
                    "ph": round(float(rec["ph"]), 4),
                })
        return {"nodes": list(nodes.values()), "edges": edges}

    def incident_ph(self, node_ids):
        """Sum of pheromone on each node's incident edges (targeted probe)."""
        if not node_ids:
            return {}
        records = self.client.run_cypher(
            """
            UNWIND $ids AS nid
            MATCH (n) WHERE (n.uid = nid OR n.uuid = nid)
            OPTIONAL MATCH (n)-[r]-()
            RETURN nid AS id, coalesce(sum(r._ph), 0.0) AS ph
            """,
            {"ids": list(node_ids)},
        )
        return {r["id"]: float(r["ph"]) for r in records}

    # ── direct metadata writes (all budgeted, all batched) ─────────────

    def evaporate(self, *, rate, floor, ceiling):
        """Global multiplicative decay on every pheromone-bearing edge."""
        records = self.client.run_cypher(
            """
            MATCH ()-[r]->() WHERE r._ph IS NOT NULL
            WITH r LIMIT $limit
            SET r._ph = CASE
                WHEN r._ph * (1.0 - $rate) < $floor THEN $floor
                WHEN r._ph * (1.0 - $rate) > $ceiling THEN $ceiling
                ELSE r._ph * (1.0 - $rate)
            END
            RETURN count(r) AS touched
            """,
            {
                "rate": float(rate), "floor": float(floor),
                "ceiling": float(ceiling),
                "limit": int(max(self.write_budget - self.writes_used, 0)),
            },
        )
        touched = records[0]["touched"] if records else 0
        self._spend(touched)
        return touched

    def deposit_edge_ph(self, deposits, *, floor, ceiling):
        """``deposits``: list of ``{"eid": elementId, "ph": new_value}``."""
        rows = [
            {"eid": d["eid"], "ph": max(float(floor), min(float(ceiling), float(d["ph"])))}
            for d in deposits
        ]
        self._spend(len(rows))
        for batch in self._batches(rows):
            self.client.run_cypher(
                """
                UNWIND $batch AS row
                MATCH ()-[r]->() WHERE elementId(r) = row.eid
                SET r._ph = row.ph, r._ph_touched = $cycle
                """,
                {"batch": batch, "cycle": self.cycle_number},
            )
        return len(rows)

    def deposit_node_material(self, deposits, *, ceiling):
        """``deposits``: list of ``{"id": uid_or_uuid, "material": add}``."""
        rows = [
            {"id": d["id"], "add": float(d["material"])} for d in deposits
        ]
        self._spend(len(rows))
        for batch in self._batches(rows):
            self.client.run_cypher(
                """
                UNWIND $batch AS row
                MATCH (n) WHERE (n.uid = row.id OR n.uuid = row.id)
                SET n._ph_material =
                        CASE WHEN coalesce(n._ph_material, 0.0) + row.add > $ceiling
                             THEN $ceiling
                             ELSE coalesce(n._ph_material, 0.0) + row.add END,
                    n._ph_touched = $cycle
                """,
                {"batch": batch, "cycle": self.cycle_number, "ceiling": float(ceiling)},
            )
        return len(rows)

    def decay_node_material(self, *, rate):
        """Slow decay of construction material (half the edge evaporation)."""
        records = self.client.run_cypher(
            """
            MATCH (n) WHERE n._ph_material IS NOT NULL
            WITH n LIMIT $limit
            SET n._ph_material = n._ph_material * (1.0 - $rate)
            RETURN count(n) AS touched
            """,
            {
                "rate": float(rate),
                "limit": int(max(self.write_budget - self.writes_used, 0)),
            },
        )
        touched = records[0]["touched"] if records else 0
        self._spend(touched)
        return touched

    def set_alarms(self, node_ids):
        rows = [{"id": i} for i in node_ids]
        self._spend(len(rows))
        for batch in self._batches(rows):
            self.client.run_cypher(
                """
                UNWIND $batch AS row
                MATCH (n) WHERE (n.uid = row.id OR n.uuid = row.id)
                SET n._ph_alarm = $cycle
                """,
                {"batch": batch, "cycle": self.cycle_number},
            )
        return len(rows)

    def clear_alarms(self, node_ids):
        rows = [{"id": i} for i in node_ids]
        self._spend(len(rows))
        for batch in self._batches(rows):
            self.client.run_cypher(
                """
                UNWIND $batch AS row
                MATCH (n) WHERE (n.uid = row.id OR n.uuid = row.id)
                REMOVE n._ph_alarm
                """,
                {"batch": batch},
            )
        return len(rows)

    def quarantine(self, node_ids):
        """Phase-1 prune: reversible marker, no data loss."""
        rows = [{"id": i} for i in node_ids]
        self._spend(len(rows))
        for batch in self._batches(rows):
            self.client.run_cypher(
                """
                UNWIND $batch AS row
                MATCH (n) WHERE (n.uid = row.id OR n.uuid = row.id)
                SET n._formica_quarantine =
                    coalesce(n._formica_quarantine, $cycle)
                """,
                {"batch": batch, "cycle": self.cycle_number},
            )
        return len(rows)

    def unquarantine(self, node_ids):
        rows = [{"id": i} for i in node_ids]
        self._spend(len(rows))
        for batch in self._batches(rows):
            self.client.run_cypher(
                """
                UNWIND $batch AS row
                MATCH (n) WHERE (n.uid = row.id OR n.uuid = row.id)
                REMOVE n._formica_quarantine
                """,
                {"batch": batch},
            )
        return len(rows)

    def clear_all_markers(self):
        """Maintenance helper (admin/tests): strip every formica marker."""
        self.client.run_cypher(
            "MATCH ()-[r]->() WHERE r._ph IS NOT NULL "
            "REMOVE r._ph, r._ph_touched"
        )
        self.client.run_cypher(
            "MATCH (n) WHERE n._ph_material IS NOT NULL OR n._ph_alarm IS NOT NULL "
            "OR n._formica_quarantine IS NOT NULL OR n._ph_touched IS NOT NULL "
            "REMOVE n._ph_material, n._ph_alarm, n._formica_quarantine, n._ph_touched"
        )
