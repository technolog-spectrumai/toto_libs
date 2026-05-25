# toto.ravioli

*(Studio only — requires BUILD_STUDIO=1)*

Sole boundary to Neo4j. All apps that need graph storage emit `GraphChangeEvent` records; a Celery worker drains them and applies upserts/deletes to Neo4j. Apps never call Neo4j directly.

## Models

- `CypherQuery` — a saved Cypher query. Fields: `name`, `slug`, `description`, `query` (Cypher text), `parameters_schema` (JSON schema for query params), `is_active`, `community` (FK, nullable — community-scoped queries).

- `CypherQueryResult` — the result of executing a `CypherQuery`. Fields: `query` FK, `parameters` (JSON), `result` (JSON), `executed_at`, `duration_ms`, `executed_by` (FK to `people.Person`).

- `GraphChangeEvent` — the queue between Django and Neo4j. Fields:
  - `event_type` — `upsert_node / delete_node / upsert_edge / delete_edge`
  - `node_label` / `edge_type` — Neo4j label or relationship type
  - `node_id` / `source_node_id` / `target_node_id` — stable string IDs
  - `properties` (JSON)
  - `status` — `pending / processing / done / failed`
  - `error_message`, `attempts`, `processed_at`
  - `source_app`, `source_model`, `source_pk` — provenance

- `GraphProjectionPlan` — a named graph projection for GDS (Graph Data Science) algorithms. Fields: `name`, `node_labels` (JSON array), `relationship_types` (JSON array), `node_properties` / `relationship_properties`, `is_active`.

- `GraphSync` — a record of a full graph synchronization run. Fields: `started_at`, `finished_at`, `status`, `events_processed`, `events_failed`, `error_log`.

## How it works

1. Any app that needs graph nodes/edges emits `GraphChangeEvent.objects.create(...)` — typically from a Django signal in `signals.py`.
2. A Celery task (`toto.ravioli.tasks.drain_graph_events`) polls for `status=pending` events in batches and applies them to Neo4j via the `bolt://neo4j:7687` connection.
3. Views execute saved `CypherQuery` records or raw Cypher for graph exploration.

## Key coupling

- All apps with graph-visible models import from `ravioli.signals` or call `ravioli.events.emit_*` helpers.
- `RAVIOLI_ENABLED` setting must be `True`; `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` must be set.
