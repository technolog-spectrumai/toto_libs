# toto.formica — ant/termite colony for Neo4j graph maintenance

Virtual ants of six castes wander the knowledge graph each cycle, lay and
follow **pheromone** on edges (usefulness trails), repair template drift,
quarantine-then-prune dead weight, and — termite-style — propose new
connections where trails keep co-visiting (stigmergy). A **Queen** reallocates
ants between castes based on colony health. Driven from the dashboard **Ops**
tab (`/formica/`, superuser-only, `BUILD_FORMICA` — implies `BUILD_GRAPH`).

Full design: [`formica.md`](../../../formica.md) at the repo root.

## Constraints honored

- **Ravioli stays the sole Neo4j boundary.** `colony/graph_ops.py` is the ONLY
  formica module importing `Neo4jClient` (batched `UNWIND` metadata writes,
  bounded sample reads). Structural mutations go through
  `bento.graph_service` — and only via reviewed proposals.
- **Tiered autonomy.** Pheromone/marker writes apply directly (reversible
  metadata). Repairs, prunes and builds become a **`FormicaProposal`**
  reviewed in the control panel — unless the colony is `trusted`, which
  auto-applies valid ops while still recording the full audit trail.
- **Budgets everywhere.** `cycle_write_budget`, `walk_sample_size`,
  `max_steps_per_ant`, `max_prunes_per_cycle`, `proposal_batch_max` — a cycle
  can never runaway-scan or runaway-write.
- **Reproducible.** Every cycle stores an `rng_seed`; walks are deterministic
  under it.

## The cycle (one epoch, sequential, celery task)

```
SENSE      bounded stale-first sample → NetworkX walk graph + scent (η)
EVAPORATE  τ ← τ·(1−rate) on all edges; material decays at half rate
SCOUT      seed τ on the stale frontier; alarm orphans
FORAGER    τ^α·η^β ε-greedy walks; batched deposit flush; ForagerReport
NURSE      bento validate_node + GraphExporter.diff_slice → repair ops
PRUNER     phase-1 quarantine marker → (grace, still cold) → delete ops
ARCHITECT  co-visits + _ph_material ≥ threshold + allowed edge type → build ops
QUEEN      reallocate ant_counts toward the neediest caste (bounded, ≥1)
```

## Graph-side markers (ephemeral by design)

`_ph` (edge), `_ph_material`, `_ph_alarm`, `_ph_touched`,
`_formica_quarantine` (node). All underscore-prefixed and excluded from
ravioli's content checksum (`graph_export.RESERVED_PREFIXES` — the one change
formica makes outside its app). SQL link projection recreates configured
relationship types, wiping edge markers — accepted: pheromone re-accrues.
Nodes marked `_historical` and `_HISTORICAL` relationships are never touched.

## Prune safety

Two-phase: a low-τ node first gets a reversible `_formica_quarantine` marker;
only after `prune_grace_cycles` of continued cold (re-probed against the live
graph) does a capped `delete_nodes` op reach the proposal. Hard exclusions:
`Colony.protected_labels`, `protected_uids`, and every `uuid`-bearing
(SQL-projected) node — deleting those is futile, reconcile recreates them.

## Ops & flags

- `BUILD_FORMICA=1` (or `manage.py --formica`); beat entry
  `formica-beat-scan` every `FORMICA_SCAN_MINUTES` (5) dispatches due
  colonies; the deployed celery stack includes a beat container.
- `manage.py ingress_formica` seeds "Colonia Prima" + default castes
  (idempotent).
- Retention: `FORMICA_REPORT_RETENTION` (200 newest cycles keep reports),
  `FORMICA_CYCLE_RETENTION` (500) — proposals are audit and never pruned.
- Control panel: overview (run/pause/trust + KPIs), parameters (form rendered
  from `PARAM_SPECS` — adding a tunable is one dict entry), cycles
  (Chart.js series), pheromone map (Cytoscape), proposals (per-op
  approve/reject → apply).

## Tests

```
cd portal && BUILD_NEO4J=1 BUILD_FORMICA=1 python manage.py test toto.formica
```

No live Neo4j needed: the cycle engine is exercised against an in-memory
`FakeGraphOps` double (same budget semantics), graph writes are patched at
`graph_service`, and walker/pheromone/params are pure. 120+ tests cover every
caste, determinism, budgets, two-phase prune, proposal apply/resume, the
beat/trigger tasks, retention, views and the checksum exclusion.
