# toto-graph

The Neo4j knowledge-graph tier of the **toto** suite: one boundary to the graph database plus everything built on top of it — bulk SQL→graph projection, a template-driven graph editor, deterministic text and API ingestion into reviewable graph patches, OCR-to-graph capture, a NeoJSON file editor, and a self-tuning "ant colony" that curates the graph over time. Every module reaches Neo4j through a single connection owned by the `ravioli` app, so reads, writes, search and analysis all share one audited boundary. This package is one of nine lockstep-versioned wheels that share the `toto.*` PEP 420 namespace; hosts install it by pinning `toto-graph` in `requirements.toto.txt`.

The package bundles eight Django apps: **ravioli**, **sql_neo4j_sync**, **bento**, **ingestor**, **connectors**, **formica**, **ocr**, and **neo_editor**. They ship together because the higher-level apps (bento, ingestor, connectors, formica) refuse to start unless the rest of the cluster is installed.

---

## What it does (functional)

toto-graph gives an operator a full workflow for building, populating, curating and querying a Neo4j knowledge graph that sits alongside the relational database.

- **Query and explore the graph.** Run saved or ad-hoc Cypher queries from the dashboard, browse results as interactive Cytoscape graphs, and search the graph by keyword, full text, or semantic similarity. NetworkX-based analyses (communities, shortest paths, neighbourhoods) answer questions relational queries handle poorly — "who is within 3 hops", "shortest path between two members", "what does this account touch".
- **Mirror your relational data into the graph.** A declarative mapping turns Django models, foreign keys and many-to-many links into graph nodes and relationships. Populate the graph one object at a time with a per-object "Export to graph" button (preview the change, then apply), or run a full bulk sync — with an optional staging mode that preserves the previous state of every changed node in a versioned history you can browse and prune.
- **Edit the graph directly.** Define node categories and edge types as templates (labels, allowed endpoints, typed property schemas) and then create, edit, filter, search and batch-delete the real nodes and relationships living in Neo4j, all from a server-rendered UI with a live graph view.
- **Turn text into graph updates.** Paste arbitrary text and get a proposed graph patch — recognised existing nodes, suggested new nodes, and suggested relationships, each with confidence, evidence and validation. Review it as a visual before/after diff, edit or approve/reject each element, then apply the approved patch to the graph.
- **Pull external APIs into the graph.** Configure a connector (which API, how to extract records, how records map onto your templates), run it on demand or on a schedule, and review each run's output as the same auditable graph diff before it is applied. Trusted connectors can auto-apply valid changes while still recording a full audit trail.
- **Capture graph data from screenshots.** OCR an uploaded image, edit the extracted text, optionally file the screenshot away, and feed the text straight into the text-ingestion review flow.
- **Edit graph documents as files.** Open `.neojson` files in a dual-pane editor — editable JSON on one side, a read-only graph preview on the other — and optionally load their contents into Neo4j.
- **Let the graph maintain itself.** An autonomous "colony" of virtual agents continuously walks the graph, reinforces useful connections, flags and (after a grace period) removes dead weight, repairs drift from the templates, and proposes new links — with everything structural routed through the same human review-and-approve flow, and hard safety budgets and exclusions throughout.

Every write path that changes graph structure is auditable: it produces a reviewable proposal or a previewable diff rather than mutating the graph silently.

---

## How it works (technical)

### Architecture at a glance

`ravioli` is the **sole boundary to Neo4j** — the only place a Bolt driver or the neomodel connection is opened. Every other app reaches the graph through it. `sql_neo4j_sync` owns the *shape* of the graph (declarative YAML) and the bulk projection logic; `bento` owns editable templates and validated node/edge CRUD; the remaining apps build features on top of those two.

Runtime dependency layering within the package:

| App | Reaches Neo4j via | In-package deps | Also uses (other toto pkgs) |
|---|---|---|---|
| `ravioli` | itself (owns the driver) | — | vault, core, ui, workflows |
| `sql_neo4j_sync` | ravioli `Neo4jClient` | ravioli | core (projected app models) |
| `bento` | ravioli (client + neomodel) | ravioli | core |
| `ingestor` | `bento.graph_service` | ravioli, bento | (spaCy, rapidfuzz) |
| `connectors` | `ingestor` → `bento.graph_service` | ravioli, bento, ingestor | api, vault |
| `formica` | `bento.graph_service` + ravioli client | ravioli, bento, sql_neo4j_sync | — |
| `ocr` | (via ingestor) | ravioli-tier | vault, ingestor |
| `neo_editor` | ravioli `neojson` + client | ravioli | vault, sql_neo4j_sync (optional) |

The higher-level apps enforce this in `AppConfig.ready()`: `bento` raises `ImproperlyConfigured` without `toto.ravioli`; `ingestor` requires ravioli + bento; `connectors` requires ravioli + bento + ingestor + `toto.api`; `formica` requires ravioli + bento + sql_neo4j_sync. That interlock is why the cluster is packaged as one wheel.

Graph features are gated behind host build flags (`BUILD_NEO4J` / `BUILD_GRAPH`, with `BUILD_CONNECTORS` and `BUILD_FORMICA` each implying the graph tier). The connection itself needs `RAVIOLI_ENABLED=True` and `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD`.

### ravioli — the Neo4j boundary

The connection, the read side, search, analysis, and per-object export.

- `connection.py` — `Neo4jClient`: raw Cypher over Bolt (`run_cypher`, `extract_graph`, node/edge CRUD, subgraph extraction), plus `is_enabled()` and local-fallback URI logic. All callers must guard with `is_enabled()`.
- `neomodel_conn.py` — configures the one neomodel connection; `ensure_configured()` is the single place `neomodel.config.DATABASE_URL` is set (from the same `NEO4J_*` settings). Raises `Neo4jDisabled` when `RAVIOLI_ENABLED` is False.
- **Models:** `CypherQuery` (a saved query: name, slug, description, Cypher text, `parameters_schema`, `is_active`, optional community scope) and `CypherQueryResult` (a cached execution with extracted nodes/edges + timing/provenance). Views provide a query browser, a Cypher console, and search endpoints rendered with Cytoscape.
- **Search:** `services/search.py` and `vector_search.py` (keyword / fulltext / semantic); `rag.py` adds a GraphRAG retriever (`build_retriever`, `run_graphrag`, `CompositeRetriever`) for embedding-backed retrieval over the graph.
- **Analysis:** `graph_analysis.py` + `predefined_tasks.py` run NetworkX analyses as workflow tasks, saving results to the vault.
- **NeoJSON:** `neojson.py` is the canonical NeoJSON (de)serialization format module reused by `neo_editor`.
- **Per-object export (`graph_export.py`, `GraphExporter`):** the `{% export_to_graph_button obj %}` tag (in `toto.core`) links to a preview page. The exporter computes an object's desired 1-hop slice from SQL (using the YAML mapping owned by `sql_neo4j_sync`), reads the matching slice from Neo4j, diffs them by content **checksum** (`new` / `changed` / `existing`), and applies non-destructively: an unchanged checksum is a no-op; a changed node is updated *and* its previous state is snapshotted into a `:_HISTORICAL` child node (`_prev_uuid`, `_historical=true`, `_archived_at`). Cross-model FK edges (related object not an instance of the declared target model) are skipped so the diff converges. `RAVIOLI_EXPORT_EXCLUDED_APPS` (default `workflows`, `fileservices`, `vault`) are never exported.
- **Review-then-apply (sync + prune):** both flows build a `GraphProjectionPlan` (sync = full diff; prune = delete-only diff of `:_HISTORICAL` snapshots) and share one apply endpoint (`graph_plan_apply`), one review modal, and one Alpine review factory. A "Staging" option on Sync archives each updated node into history before overwriting. The **History** tab renders kept snapshots as version chains coloured by a keep-depth control; **Prune history** keeps the N newest snapshots per node (default `RAVIOLI_DEFAULT_MAX_HISTORY`).

### sql_neo4j_sync — SQL→Neo4j projection

Decides *what* the graph contains; performs no Bolt I/O of its own.

- **Graph shape (YAML):** `graph/*.yaml`, one file per projected app (events, kanban, socialhub, locations, polls, core). Each declares `nodes:` (label, dotted `model` path, `uuid_field`, and a property `fields:` map with optional `transform:` — `wkt` / `str` / `json` / `file_url` / `default_dict`) and `links:` (`from_label` / `to_label` / `relation` with either an FK/M2M `source` + `cardinality`, or a `via_model` junction that can carry edge props and resolve a `generic: true` GenericForeignKey at runtime). `loader.load_all_configs()` skips configs for uninstalled apps; `loader.validate_configs()` checks every referenced model/field exists (no Neo4j needed); `loader.label_for_model()` is the model→label registry the export button consults.
- **Projection:** `ProjectionRunner` reads the configs and runs the corresponding `MERGE` Cypher through a ravioli `Neo4jClient`.
- **Models:** `GraphChangeEvent` (the outbox row written when `RAVIOLI_AUTO_SYNC` is on — `action`, `graph_label`, `model_path`, `object_uuid`, `payload`, `status`, `attempts`, `error`), `GraphProjectionPlan` (a computed expected-vs-actual diff), `GraphSync` (admin trigger surface, no rows of its own), and `GraphSyncSchedule` (singleton periodic-sync config).
- **Opt-in auto-sync (off by default):** only when `RAVIOLI_AUTO_SYNC=True` does `signals.register_graph_signals()` wire `post_save` / `post_delete` / `m2m_changed` on mapped models to enqueue outbox rows. `ravioli_sync_pending` (or a Celery task) drains them via `process_graph_event`; `ravioli_rebuild` / `ravioli_reconcile` do full resync / drift repair.

### bento — template-driven graph editor

Templates live in SQL; the actual nodes and relationships live in Neo4j.

- **Models:** `BentoCategory` (a Neo4j label + a `property_schema` list of `{name, type, required, label, help}`) and `BentoEdgeType` (a relationship type with allowed source/target categories and its own property schema); both subclass `DomainEntity`. Supported property types: `string, text, integer, float, boolean, datetime, json`, plus a free-form `extra` JSON bag on every node/edge.
- **Dynamic registry (`registry.py`):** each template row is turned into a neomodel `StructuredNode` / `StructuredRel` subclass via `type()` — template definitions are treated as **data only**, never `eval`/`exec`. Classes are cached per slug, keyed on the template's `updated_at`, and invalidated by `post_save`/`post_delete` signals so every worker rebuilds deterministically from the same DB state.
- **Neo4j access (`graph_service.py`):** the only bento module that touches Neo4j. It uses the dynamic neomodel classes for typed create/update (schema validation + `uid` generation) and ravioli's `Neo4jClient` for listing/search/pagination, edges, batch delete and lazy extraction. Bento requires ravioli and calls `ravioli.neomodel_conn.ensure_configured()`. When `RAVIOLI_ENABLED` is False, operations raise `GraphUnavailable` and views render a "graph unavailable" page.
- **Template↔category binding:** a node's category is keyed off its **Neo4j label**, so nodes written by the SQL→Neo4j sync (`:KanbanTask`, `:Person`, …) are recognised. `ingress_bento` derives a `BentoCategory` per graph label and a `BentoEdgeType` per relation from the `sql_neo4j_sync` YAML configs; `--full` additionally seeds demo templates + a sample graph, and `SEED_GRAPH_TYPES` seeds a minimal `concept`/`note`/`references` starter set. (Bento addresses nodes by `uid` while the sync writes `uuid`, so synced node *types* are recognised but per-node editing of synced nodes needs the identifiers aligned.)
- **UI:** server-rendered (Tailwind + Alpine) with a lazy Cytoscape graph; node/edge lists are paginated, type-filterable (`?category=` / `?edge_type=`), quick-searchable (`?q=`) and support multi-select batch delete. Templates have CRUD screens and are registered in the Django admin. Bento has **no** quota integration.
- **Legacy migration:** `migrate_bento_to_neo4j` moves content from the removed SQL `IdeaBox`/`IdeaLink` tables into Neo4j (a no-op when absent).

### ingestor — text → validated graph patch

Surfaced as the **Ingestor** tab in Ravioli (`/ingestor/`, superuser-only). **Deterministic, no LLMs.**

- **Pipeline (`services/pipeline.build_proposal_dict(text)`):** (1) *catalog* existing nodes via `graph_service.iter_nodes`, turning searchable properties + aliases into match entries; (2) *detection* with spaCy — an `EntityRuler` built from the catalog detects existing nodes (pattern id = uid), the model's NER suggests new entities mapped via `INGESTOR_SPACY_LABEL_MAP` (degrades to a blank pipeline with no model); (3) *matching* with rapidfuzz (difflib fallback) flags duplicates/merge candidates; (4) *relations* — per sentence, only edges `graph_service.edge_types_between` allows, gated/boosted by `BentoEdgeType.trigger_lemmas`; (5) *scoring* via deterministic formulas; (6) *validation* against the live Bento templates; (7) *proposal* assembly into editable patch JSON.
- **Model:** `IngestProposal` holds the `proposal` JSON (nodes/relationships referencing each other by `temp_id`, each with kind/category/uid/evidence/confidence/match/validation/approval). Editing any element re-validates it server-side.
- **Apply (`services/apply.run`):** writes approved + valid elements through bento; **idempotent/resumable** — created uids and applied relationship ids are recorded in `IngestProposal.apply_result` so retries never duplicate work.
- **Config knobs (`services/config.py`):** `INGESTOR_SPACY_MODEL` (default `en_core_web_sm`), `INGESTOR_SPACY_LABEL_MAP`, `INGESTOR_CATALOG_MAX_NODES` (20000), `INGESTOR_DUP_THRESHOLD` (0.90), `INGESTOR_CANDIDATE_THRESHOLD` (0.70), `INGESTOR_FUZZY_MAX_CANDIDATES` (5000). Without the spaCy NER model the ingestor still runs (existing-entity detection + relationships) but won't suggest uncategorised new entities.

### connectors — external APIs → validated graph patches

Surfaced as the **Connectors** tab in Ravioli (`/connectors/`, superuser-only). **Deterministic, no LLMs.** Opens **no** Neo4j connection of its own — reads go through the ingestor catalog / `bento.graph_service`, writes only through `ingestor.services.apply`.

- **Models:** `DataConnector` (which API, `extract_config`, `mapping_spec`, `schedule_enabled` + `interval_minutes`, trust flag) and `ConnectorRun` (per-run status/stats/logs). A run flows `pending → running → review | applied | empty | failed`.
- **Run (`services/runner.execute_run`):** *extract* pages via `toto.api.client.execute_api_request` (auth injection, SSRF host allowlist, 1 MiB / 30 s caps; pagination `none | page | offset | cursor`) → *archive* every page as a JSON `VaultFile` in the `connectors-archive` bucket plus an auth-free `request_log` → *transform* records through `mapping_spec` (within-run dedupe by category + normalized identifier, graph dedupe via the ingestor catalog + rapidfuzz, Bento-constrained relationships, skipping existing→existing edges already present) → *persist* as an ingestor proposal (`ready`, run `review`) → optional *trusted auto-apply* (`approve_all_valid` + idempotent `apply.run`). A human apply via the ingestor UI flips `review → applied` through a `post_save` signal on `IngestProposal`.
- **`mapping_spec`** declares `nodes` (each with a unique `rule_id`, a `category_slug`, an `identifier` surface, and `properties` where each value is exactly one of a dot-path / `const` / `template`, plus an optional `when` guard) and `relationships` (each with `edge_type_slug`, `from_rule`/`to_rule` — both must fire on the same record — with endpoints checked against the edge type's allowed sources/targets). Specs are validated structurally and against live Bento templates (`services/mapping.validate_mapping_spec`).
- **`extract_config`** (kind `rest_api`): `endpoint` (relative to the connector's `base_url`), `method`, static `params`/`headers`, `records_path`, `pagination`, `max_records`, `timeout_seconds`.
- **Scheduling:** a single global beat task (`connectors_scan_schedules`, every `CONNECTORS_SCAN_MINUTES`) claims due connectors under a row lock — `next_run_at` advances before dispatch, in-flight runs never stack, and an untrusted connector with a proposal still awaiting review is skipped. Runs stuck beyond `STALE_RUN_MAX_AGE` (2 h) stop blocking their connector.
- **Secrets** never live here: auth config/credentials belong to the `api.Connector` (Gervazy `EncryptedSecret` in the shared strongbox, which needs `SABBIA_VAULT_PASSWORD` on web + worker and a one-time `connectors_init_vault`). `ingress_connectors --full` seeds a no-auth OpenAlex demo mapped onto the seeded `note`/`concept` templates.

### formica — autonomous graph-maintenance colony

An ant/termite colony curating the ravioli/bento graph. Driven from the **Ops** tab (`/formica/`, superuser-only, `BUILD_FORMICA`).

- **Cycle (one epoch, a sequential Celery task):** `SENSE` (bounded stale-first sample → NetworkX walk graph + scent) → `EVAPORATE` (pheromone τ and material decay) → `SCOUT` (seed τ on the stale frontier) → `FORAGER` (τ^α·η^β ε-greedy walks, batched deposit) → `NURSE` (bento validate + diff → repair ops) → `PRUNER` (two-phase quarantine → delete) → `ARCHITECT` (co-visits + material ≥ threshold + allowed edge type → build ops) → `QUEEN` (reallocate ant counts toward the neediest caste).
- **Models:** `Colony`, `CasteConfig`, `ColonyCycle`, `FormicaProposal`, `ForagerReport`.
- **Boundaries & safety:** `colony/graph_ops.py` is the ONLY formica module importing `Neo4jClient` (batched `UNWIND` metadata writes, bounded sample reads); structural mutations go through `bento.graph_service` and only via reviewed `FormicaProposal`s (a `trusted` colony auto-applies valid ops while still recording the audit trail). Pheromone/marker writes are direct (reversible metadata). Everything is bounded (`cycle_write_budget`, `walk_sample_size`, `max_steps_per_ant`, `max_prunes_per_cycle`, `proposal_batch_max`) and reproducible (each cycle stores an `rng_seed`).
- **Graph markers** are ephemeral and underscore-prefixed (`_ph`, `_ph_material`, `_ph_alarm`, `_ph_touched`, `_formica_quarantine`), excluded from ravioli's content checksum. Two-phase prune: a low-τ node first gets a reversible `_formica_quarantine` marker, and only after `prune_grace_cycles` of continued cold does a capped delete op reach a proposal. Hard exclusions: `Colony.protected_labels`, `protected_uids`, and every `uuid`-bearing (SQL-projected) node.
- **Ops:** beat entry `formica-beat-scan` (every `FORMICA_SCAN_MINUTES`, default 5) dispatches due colonies; `ingress_formica` seeds "Colonia Prima" + default castes (idempotent). Retention: `FORMICA_REPORT_RETENTION` (200) and `FORMICA_CYCLE_RETENTION` (500); proposals are never pruned. Tunables are one dict entry each in `PARAM_SPECS`.

### ocr — screenshot → text → graph

A small, **stateless** OCR sub-tab in the Ravioli tab bar (`BUILD_NEO4J=1`; needs the `tesseract-ocr` binary + `pytesseract`). No database models — pure request/response. `ocr.py`'s `OcrHelper` wraps pytesseract (run + line grouping); `ocr_run` runs OCR and optionally saves the screenshot as an `image` `vault.VaultFile` in a chosen bucket; the extracted (editable) text is POSTed to `ingestor:generate`, landing the user on the resulting ingestor proposal.

### neo_editor — dual-mode NeoJSON file editor

Hosted as its own app so both the vault (via a `VaultEditorPlugin` "Edit" button, key `neojson`, for `.neojson` files) and ravioli (the query → NeoJSON export "Open in editor" link) can reuse it. No models. `neojson_editor_view` renders an Ace JSON editor (editable) beside a read-only Cytoscape preview; `neojson_save_view` validates the document with `ravioli.neojson.loads`/`validate` before overwriting the vault file; `neojson_load_view` loads the graph into Neo4j in `merge` or `replace` mode via `sql_neo4j_sync.graphsync.load_neojson` (degrading gracefully if that optional app is absent, and refusing when `RAVIOLI_ENABLED` is False). The NeoJSON format itself lives in `toto.ravioli.neojson`.

### Notable design decisions

- **One driver, one boundary.** Only ravioli opens Neo4j; every other app reaches the graph through `Neo4jClient` or `bento.graph_service`. This keeps connection config, disable-handling and the content checksum in one place.
- **Structure changes are reviewable.** Ingestor, connectors and formica emit proposals or previewable diffs, never silent mutations; apply paths are idempotent/resumable and keep an audit trail.
- **Non-destructive history.** Changed nodes are snapshotted into `:_HISTORICAL` version chains rather than overwritten; pruning history is a separate, explicit review flow.
- **Templates as data.** Bento builds neomodel classes from template rows via `type()` (never `eval`/`exec`), cached and signal-invalidated for cross-worker consistency.
- **Deterministic ingestion.** Text and API ingestion use spaCy/rapidfuzz and declarative mappings — no LLMs in the write path — so runs are reproducible and auditable.

---

## Usage

### Install

toto-graph is normally installed as part of the toto suite by pinning it in a host's `requirements.toto.txt` (all nine `toto-*` wheels move in lockstep at the same version). To work on it directly from the monorepo:

```bash
pip install -e toto_libs/packages/toto-graph
```

The apps are Django apps under the `toto.*` namespace. Add the ones you need to `INSTALLED_APPS` (respecting the dependency order — ravioli first, then sql_neo4j_sync / bento, then the rest), or let the host's build flags do it. Because bento/ingestor/connectors/formica assert their dependencies in `ready()`, install the whole cluster together.

### Configure

- Enable the graph tier with the host build flags (`BUILD_NEO4J` / `BUILD_GRAPH`; `BUILD_CONNECTORS` and `BUILD_FORMICA` imply the graph tier).
- Set `RAVIOLI_ENABLED=True` and the connection: `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`.
- For text ingestion, install a spaCy model: `python -m spacy download en_core_web_sm`.
- For OCR, install the `tesseract-ocr` binary + language packs (plus `pytesseract`).
- For authenticated connectors, set `SABBIA_VAULT_PASSWORD` on web and worker and run `manage.py connectors_init_vault`.
- For scheduled connectors/formica cycles, run a Celery worker and beat (deployments provision a dedicated beat container).

### Common commands

```bash
# Seed bento templates from the SQL→Neo4j YAML (+ demo graph with --full)
python manage.py ingress_bento --full

# Migrate legacy SQL IdeaBox/IdeaLink data into Neo4j (no-op if tables absent)
python manage.py migrate_bento_to_neo4j --dry-run

# Bulk SQL→Neo4j projection / drift repair, and drain the auto-sync outbox
python manage.py ravioli_rebuild
python manage.py ravioli_reconcile
python manage.py ravioli_sync_pending

# Seed a demo connector (no-auth OpenAlex) and a formica colony
python manage.py ingress_connectors --full
python manage.py ingress_formica
```

### Import surface

```python
from toto.ravioli.connection import Neo4jClient, is_enabled   # the only Neo4j driver
from toto.bento import graph_service                           # validated node/edge CRUD
from toto.ingestor.services import pipeline, apply             # text → proposal → apply
from toto.ravioli import neojson                               # NeoJSON (de)serialization
```

Always guard graph access with `is_enabled()`; never open a Bolt driver outside ravioli.

### Develop & test

Each app ships its own tests, runnable from the host `portal/`. No live Neo4j, network, or spaCy model is required — HTTP, graph writes and the catalog are patched at their seams, and formica's cycle engine runs against an in-memory `FakeGraphOps` double.

```bash
cd portal && BUILD_NEO4J=1 python manage.py test toto.ingestor
cd portal && BUILD_NEO4J=1 BUILD_CONNECTORS=1 python manage.py test toto.connectors toto.ingestor
cd portal && BUILD_NEO4J=1 BUILD_FORMICA=1 python manage.py test toto.formica
```

---

## Build & packaging

toto-graph is one of the nine lockstep-versioned wheels in the toto suite. All nine share a single `VERSION` (currently `1.6`) rewritten only by the repo's release script — never edited by hand — and each pins its siblings at the exact same version. This package depends on three siblings: **toto-base**, **toto-flow**, and **toto-ai** (all `==1.6`).

It builds with setuptools from a `src/` layout using PEP 420 namespace packages; each toto wheel owns exactly the portion of `src/toto/` present in its own tree, and a repo script enforces that the partition stays disjoint. Package data ships templates, static assets, and the `graph/*.yaml` projection configs.

For the full build, versioning, pinning and release workflow, see the repository root README.
