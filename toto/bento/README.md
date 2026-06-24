# Bento

Bento is a **first-class Neo4j graph editor**. You define *node-category* and
*edge-type* **templates** in SQL; the actual nodes and relationships live in
**Neo4j**. Bento builds neomodel classes from the templates at runtime and edits
the graph through them.

## Source of truth

| Lives in | What |
|---|---|
| **SQL** (`BentoCategory`, `BentoEdgeType`) | *Templates only* — labels, relationship types, allowed endpoints, typed property schemas, UI metadata. |
| **Neo4j** | The real data — every node `(:Label {uid, …props, extra})` and relationship `[:REL_TYPE {…props, extra}]`. |

`BentoCategory` defines a Neo4j label + a `property_schema` (a list of
`{name, type, required, label, help}`). `BentoEdgeType` defines a relationship
type, allowed source/target categories, and its own property schema. Supported
property types: `string, text, integer, float, boolean, datetime, json` — plus a
free-form `extra` JSON bag on every node/edge for ad-hoc keys.

### Templates synced from the SQL→Neo4j sync

`ingress_bento` (in **every** mode, including non-`--full`) derives a `BentoCategory`
for each graph node label and a `BentoEdgeType` for each relation declared in
`toto.sql_neo4j_sync`'s `graph/*.yaml` configs (property schema mapped from the
YAML field map + transforms; edge allowed-sources/targets unioned per relation).
Bento keys a node's category off its **Neo4j label**, so nodes the SQL→Neo4j sync
writes (`:KanbanTask`, `:Person`, …) are recognised as these categories. `--full`
additionally seeds the demo `idea/source/question` templates + sample graph.

### Minimal graph types (`SEED_GRAPH_TYPES`)

With `--seed-graph-types` (or `settings.SEED_GRAPH_TYPES` / the `SEED_GRAPH_TYPES=1`
deploy env var), `ingress_bento` also seeds — in **every** mode, including non-`--full`
— a `concept` and a `note` `BentoCategory` plus a `references` `BentoEdgeType`
linking `note → concept`. SQL templates only (no Neo4j writes), so it's fast and
safe in a thin bring-up; gives the graph editor a starter set to exercise. Off by
default.

> Note: bento addresses nodes by a `uid` property, while the sync writes `uuid` —
> so synced node *types* are recognised, but per-node editing of synced nodes in
> bento needs the identifiers aligned (follow-up).

## Dynamic neomodel registry

[`registry.py`](registry.py) turns each template row into a neomodel
`StructuredNode` / `StructuredRel` subclass via `type()` (template definitions are
treated as **data only** — never `eval`/`exec`). Classes are cached per slug and
keyed on the template's `updated_at`; the cache is invalidated by
`post_save`/`post_delete` signals ([`signals.py`](signals.py)) so every worker
rebuilds deterministically from the same DB state.

## Neo4j access

All graph I/O is isolated in [`graph_service.py`](graph_service.py) — the only
module that touches Neo4j. It uses:

* **neomodel** dynamic classes for typed node create/update (schema validation +
  `uid` generation), and
* ravioli's `Neo4jClient` (raw Cypher) for listing/search/pagination, edges,
  batch delete and lazy graph extraction.

**ravioli owns the connection.** Bento requires `toto.ravioli` to be installed
(`BentoConfig.ready()` raises otherwise) and calls
`toto.ravioli.neomodel_conn.ensure_configured()` — the single place
`neomodel.config.DATABASE_URL` is set, from the same `NEO4J_*` settings
`Neo4jClient` uses. When `RAVIOLI_ENABLED` is False, every operation raises
`GraphUnavailable` and the views render a "graph unavailable" page instead of
crashing.

## UI

Server-rendered (Tailwind + Alpine) with a lazy Cytoscape graph (tap a node to
expand its neighborhood). Node and edge lists are **paginated**, filterable by
**type** (`?category=` / `?edge_type=`), **quick-searchable** (`?q=`), and support
multi-select **batch delete**. Templates (categories, edge types) have their own
CRUD screens and are registered in the Django admin.

## No quota

Bento has **no** quota integration — no `check_quota()` / `record_usage()`.

## Migrating legacy SQL data

The old SQL models (`IdeaBox`/`IdeaLink`) are removed. If a legacy database still
has their tables, migrate the content into Neo4j (nothing is deleted):

```
python manage.py migrate_bento_to_neo4j --dry-run
python manage.py migrate_bento_to_neo4j --category <slug> --edge-type <slug>
```

The command reads the legacy tables via raw SQL and is a no-op when they're
absent.

## Seeding example templates

```
python manage.py ingress_bento --full
```
