# toto.sql_neo4j_sync

*(Neo4j only — requires BUILD_NEO4J=1)*

The SQL → Neo4j **projection** layer. Owns the *shape* of the graph (YAML configs) and the logic that mirrors Django/SQL rows into Neo4j nodes and relationships. All Neo4j I/O goes through `toto.ravioli` — this app never opens a Bolt connection itself.

## Purpose

`ravioli` is the connection/query boundary; `sql_neo4j_sync` decides *what* the graph contains. Each `graph/*.yaml` file declares, per app, which models become nodes (label + uuid field + property map) and which foreign keys / M2M / junction models become relationships. `ProjectionRunner` reads those configs and runs the corresponding `MERGE` Cypher through a `ravioli.connection.Neo4jClient`.

This app owns two things: the **YAML graph shape** (consumed by both paths) and the **bulk projection** (full/selective resync + the planner's expected-vs-actual diff). Bulk projection is **opt-in** — nothing reaches Neo4j on save/delete unless `RAVIOLI_AUTO_SYNC` is enabled.

The primary, user-facing way to populate the graph — the per-object **"Export to graph"** button, with its Cytoscape preview and history-preserving apply — lives in **`toto.ravioli`** (`graph_export.py`); it reuses the YAML mapping here. See ravioli's README.

## Graph shape (YAML)

`graph/*.yaml` — one file per app (events, kanban, socialhub, locations, polls, core). Loaded by `loader.load_all_configs()` (configs for uninstalled apps are skipped). Each file has:

- `nodes:` — `label`, `model` (dotted path), `uuid_field`, and `fields:` (neo property → SQL field, with optional `transform:` — `wkt` / `str` / `json` / `file_url` / `default_dict`).
- `links:` — `from_label` / `to_label` / `relation`, plus either `source` (an FK or M2M field, with `cardinality`) or `via_model` (a junction model carrying edge props; `generic: true` resolves a GenericForeignKey target at runtime).

`loader.validate_configs()` checks every model/field the YAML references actually exists — no Neo4j connection required.

## Models

- `GraphChangeEvent` — the outbox row written when `RAVIOLI_AUTO_SYNC` is on. Fields: `action` (`upsert_node` / `delete_node` / `resync_links` / `resync_link_source` / `resync_link_full` / `upsert_junction` / `delete_junction`), `graph_label`, `model_path`, `object_uuid`, `payload` (JSON — carries `link_key`), `status` (`pending` / `processing` / `done` / `failed`), `attempts`, `error`, timestamps.
- `GraphProjectionPlan` — a computed diff (expected vs. actual graph) for full-resync / reconcile operations. Fields: `status`, `scope`, `summary`, `diff` (JSON), `error`, timestamps.
- `GraphSync` — admin trigger surface for a full projection run (holds no rows of its own).
- `GraphSyncSchedule` — singleton config for periodic background sync: `enabled`, `interval_minutes`, `last_run_at`, `last_run_status`, `last_error`.

## How it works

**Per-object export (primary path) — owned by ravioli.**
The `{% export_to_graph_button obj %}` tag (in `toto.core`) links to ravioli's preview/apply flow, which reads this app's YAML mapping but performs a checksum-gated, history-preserving 1-hop diff/apply. See `toto.ravioli/graph_export.py` and its README.

**Opt-in auto-sync (off by default).**
When `RAVIOLI_AUTO_SYNC=True`, `signals.register_graph_signals()` wires `post_save` / `post_delete` / `m2m_changed` on every mapped model to enqueue `GraphChangeEvent` rows. The management command `ravioli_sync_pending` (or a Celery task) drains them via `process_graph_event` → `ProjectionRunner`; `ravioli_rebuild` / `ravioli_reconcile` do full resync / drift repair.

**Admin.** The `GraphSync` changelist offers a "run sync" action that projects selected labels (or everything) via `ProjectionRunner.run_with_progress`.

## Key coupling

- All Neo4j writes go through `toto.ravioli.connection.Neo4jClient`; `RAVIOLI_ENABLED` must be True.
- `loader.label_for_model()` is the model → graph-label registry used by the `graph_export` template tag in `toto.core` (to decide whether to show the button); ravioli's `GraphExporter` builds its own registry from the same configs.
- Source data comes from the projected apps (events, kanban, socialhub, locations, polls, core) via the dotted `model:` paths in the YAML — there is no code-level import coupling.

## Dependencies

`ravioli` (Neo4j connection). Reads — but does not import — the models of the apps it projects.
