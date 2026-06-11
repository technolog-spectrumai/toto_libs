# toto.ravioli

*(Neo4j only — requires BUILD_NEO4J=1)*

Sole boundary to Neo4j. Every app that touches the graph goes through `ravioli.connection.Neo4jClient` (raw Cypher over Bolt) or the single neomodel connection ravioli configures — no other app opens its own driver. Ravioli owns the **connection, saved queries, search, graph analysis, and the per-object "Export to graph" sync** (`graph_export.py`). The graph *shape* (which models/fields/links become nodes and edges) is declared as YAML in the sibling app `toto.sql_neo4j_sync`, whose bulk projection/planner ravioli reuses.

## Purpose

The graph layer answers questions relational queries handle poorly: "communities within 3 hops", "shortest path between two members", "what instruments does this account touch". Ravioli provides the connection plus the read side — stored `CypherQuery` records admins run from the dashboard, keyword/fulltext/semantic search, NetworkX-based graph analysis, and NeoJSON import/export.

## Models

- `CypherQuery` — a saved Cypher query: `name`, `slug`, `description`, `query` (Cypher text), `parameters_schema`, `is_active`, optional `community` scope.
- `CypherQueryResult` — a cached execution of a `CypherQuery` (extracted nodes/edges + timing/provenance).

> The graph outbox/projection models — `GraphChangeEvent`, `GraphProjectionPlan`, `GraphSync`, `GraphSyncSchedule` — now live in **`toto.sql_neo4j_sync`**, not here.

## Modules

- `connection.py` — `Neo4jClient` (`run_cypher`, `extract_graph`, node/edge CRUD, subgraph) + `is_enabled()` + local-fallback URI logic.
- `graph_export.py` — `GraphExporter`: the per-object "Export to graph" engine. Reads the YAML mapping from `sql_neo4j_sync`, but owns the read-diff-write sync (see below).
- `neomodel_conn.py` — configures the one neomodel connection; raises `Neo4jDisabled` when `RAVIOLI_ENABLED` is False.
- `neojson.py` — NeoJSON (de)serialization for graph documents.
- `services/search.py`, `vector_search.py` — keyword / fulltext / semantic search.
- `services/graph_plans.py` — build & apply `GraphProjectionPlan`s: `create_sync_plan` (full SQL→Neo4j diff), `create_prune_plan(keep)` (delete `:_HISTORICAL` snapshots beyond the N newest per node, as a delete-only plan), `apply_plan`, `plan_payload`. Backs both review flows.
- `graph_analysis.py`, `predefined_tasks.py` — NetworkX analysis run via workflows, results saved to the vault.
- `views.py` — query browser, Cypher console, search, graph-analysis endpoints (Cytoscape front-end), the per-object export preview/apply, the **"Neo4j is not running"** health probe, **"Sync all to graph"** (`graph_sync_plan`, Knowledge Graph tab), and the **History** tab (`history_view` + `history_data`, with **Prune history** = `graph_prune_plan`). All review-then-apply actions apply via one endpoint (`graph_plan_apply`).

### Review-then-apply (sync + prune)

Both share the same machinery so nothing is duplicated:
- a `GraphProjectionPlan` (sync = full diff; prune = delete-only diff of `:_HISTORICAL` snapshots),
- one apply endpoint `graph_plan_apply` (`apply_projection_plan` → `ProjectionPlanApplier`); a **Staging** checkbox on Sync (`staged=1`) first snapshots each *updated* node's current state into `:_HISTORICAL` (`graph_plans.apply_plan(staged=True)` → `archive_node_to_history`) before overwriting, so old values are pushed to history instead of destroyed,
- one review modal partial **`templates/ravioli/_review_modal.html`** (Cytoscape diff graph + itemized list + Apply/Cancel),
- one shared Alpine factory **`templates/ravioli/_review_flow.html`** (`reviewFlow()` → `openReview`/`applyReview`/`closeReview`/`renderReviewGraph`), spread into both the Knowledge Graph and History page components.

The **History** tab (`history.html`) shows every kept `:_HISTORICAL` snapshot as a Cytoscape graph (canonical nodes + version chains), coloured by a **keep-depth** control: versions beyond the depth are flagged prunable (red). **Prune history** opens the same review modal for the delete-only plan, keeping the N newest snapshots per node (default `RAVIOLI_DEFAULT_MAX_HISTORY`).

## How it works

1. Callers construct `Neo4jClient()`, run Cypher (`MERGE` upserts / `MATCH` reads), and `close()`. Always guard with `is_enabled()`.
2. Views execute saved `CypherQuery` records or raw Cypher and render results with Cytoscape.

### Per-object "Export to graph" (`graph_export.py`)

The `{% export_to_graph_button obj %}` tag (in `toto.core`) links to a **preview** page; the user reviews the slice in Cytoscape, then applies. For one object the exporter:

1. computes the desired **1-hop slice** from SQL — the object node plus the neighbours its outgoing FK/M2M links point to, and those edges (uses the YAML mapping from `sql_neo4j_sync`; junction/`via_model` links are out of scope);
2. reads the matching slice currently in Neo4j;
3. diffs them — each node's status is decided by a content **checksum** (`new` / `changed` / `existing`), each edge is `new` / `existing`;
4. **applies without destroying prior state**: an unchanged checksum is a no-op; a changed one updates the canonical node *and* snapshots its previous state into a `:_HISTORICAL` child node (fresh uuid; markers are underscore-prefixed — old uuid in `_prev_uuid`, plus `_historical=true`, `_archived_at`). Snapshots reconnect to their canonical via `relink_orphan_history` when it reappears; orphans (canonical permanently gone) stay listed/prunable in the History tab. History is never capped here — pruning is the separate, manual **Prune history** review flow above. The root's outgoing edges are merged and stale ones (declared relations) removed.

A FK link only produces an edge when the related object is actually an instance of the declared target node's model; a cross-model FK (e.g. an `assignee` FK to `Practitioner` declared as `-> Person`) is skipped so the diff converges instead of proposing an edge to a node that can't exist.

`RAVIOLI_EXPORT_EXCLUDED_APPS` (default `workflows`, `fileservices`, `vault`) are never exported. Historical snapshots carry `_historical=true` so the bulk `sql_neo4j_sync` full-sync skips them.

> Bulk SQL→Neo4j projection (and opt-in auto-sync on save/delete) is a separate, declarative path owned by `sql_neo4j_sync` — see that app's README.

## Key coupling

- Sole Neo4j boundary: `sql_neo4j_sync`, `bento`, and `neo_editor` all go through ravioli; no other app imports a Neo4j driver.
- `RAVIOLI_ENABLED` must be `True`; `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` configure the connection (see `neo4j.md`).

## Dependencies

None — standalone Neo4j boundary. (`sql_neo4j_sync`, `bento`, and `neo_editor` depend on it.)
