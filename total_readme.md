# toto

**toto** is a Django-based platform for running tokenized community economies. Communities on toto can issue assets, vote on rules, levy taxes, trade, run projects, learn, and settle disputes — all tied together through a shared identity and cryptographic foundation.

---

## The Idea

A community on toto operates like a mini-state: it has members, money, rules, a parliament, a court, and an economy. Everything economically meaningful can be tokenized and tracked on a double-entry ledger. Governance decisions are immutable and hash-chained. Secrets are encrypted at rest with a per-user vault. The graph of all relationships lives in Neo4j, synchronized from Postgres through a single boundary app.

---

## System Traits

- **Django monorepo.** Django projects: `toto/` (platform) and `portal/` (management portal).
- **Double-entry ledger.** Every balance change posts to an immutable `LedgerEntry`. A hash-chain ties every transaction to the previous one — tamper evidence at the DB level.
- **Lapis smart contracts.** A YAML-based contract VM in `assets.Contract`. Financial instruments generate Lapis programs; the VM validates state transitions.
- **Hierarchical encryption.** `gervazy` implements a three-tier AES-256-GCM key hierarchy: password → Argon2id KDF → UKEK → VMK → DEK → encrypted objects (secrets, files, private keys).
- **OIDC provider.** `sso_master` is a full OpenID Connect server. Signing keys are RSA stored encrypted in gervazy.
- **Graph layer.** `ravioli` is the sole Neo4j boundary and owns the per-object "Export to graph" sync: it computes an object's 1-hop slice, diffs it against Neo4j by content checksum, previews it in Cytoscape, and applies it without destroying prior state (changed nodes are snapshotted into capped `:HISTORICAL` versions). The graph *shape* is declared as YAML in `sql_neo4j_sync`, which also runs the opt-in bulk projection. No app ever calls Neo4j directly.
- **Real-time over WebSockets.** Django Channels powers chat (enigma), whiteboard (sketch), LaTeX compilation (texlab), and compute kernels (mandragora).
- **Community-governed economy.** Each community runs an Assembly where members vote on proposals. Passed proposals enact `CommunityRule`, `CommunityTransactionFee`, or `PollTax` objects.
- **Celery + Redis.** Background tasks handle ledger operations, graph sync, subscription billing, instrument lifecycle, workflows, and periodic tax collection.
- **Modular deployment.** `deploy.py` reads YAML configs to build settings for each deployment target.

---

## Deployment: Base vs Studio

The single most important architectural divide is `BUILD_STUDIO`.

| | Base (`portal_mini`) | Studio (`portal_neo`) |
|---|---|---|
| **Server** | gunicorn (WSGI) | uvicorn/daphne (ASGI) |
| **WebSockets** | ✗ | ✓ (Django Channels + Redis) |
| **Neo4j** | ✗ | ✓ (via ravioli) |
| **Compute kernels** | ✗ | ✓ (mandragora + ZMQ) |
| **Infrastructure** | Postgres + Redis + Nginx | + Neo4j + ZMQ kernel server |

Studio apps (only when `BUILD_STUDIO=1`): `enigma`, `ravioli`, `texlab`, `mandragora`, `workflows`, `weather`, `sketch`, `ocr`, `steven`, `travels`.

---

## App Map

### Foundation
| App | Purpose |
|---|---|
| `core` | Platform config root. `Platform`, `Theme`, `Federation`, `DomainEntity` base, plugin registry. |
| `api` | Outbound API connector registry. Credentials stored in gervazy, never inline. |
| `backup` | Platform backup signing and archive storage. |
| `ingress` | Base management command for data ingestion (`DATA_ROOT`, `--full` flag). |
| `ui` | Shared template tags, context processors, and base page layout. |

### Security
| App | Purpose |
|---|---|
| `gervazy` | Encryption-at-rest. Three-tier AES-256-GCM key hierarchy per user. |
| `vault` | User file storage. Buckets, soft-delete VaultFiles, FileGateway sharing. |

### Identity
| App | Purpose |
|---|---|
| `people` | Primary identity object. `Person` is the human actor across all domains. |
| `sso_core` | Shared OIDC manifest schemas — pure Python dataclasses, no models. |
| `sso_master` | Full OIDC 1.0 provider. Issues tokens, manages signing keys. |
| `sso_client` | OIDC consumer config. Stores the provider endpoint imported from sso_master. |

### Community & Governance
| App | Purpose |
|---|---|
| `socialhub` | Community model. Membership, news, hierarchy, administrata view. |
| `events` | Scheduled event calendar. EventInvite and Availability blocks. |
| `polls` | Informal community polling. No governance effect. |
| `assembly` | Democratic governance engine. Proposals → votes → hash-chained decisions → enacted rules/fees/taxes. |
| `magistrate` | Elected community officials. Decisions, fines, misconduct reports. |
| `tribunal` | Dispute resolution. Case filing → jury → ruling → enforcement. |

### Economy
| App | Purpose |
|---|---|
| `assets` | Core ledger. Asset minting, accounts, double-entry transactions, obligations, Lapis contracts. |
| `claims` | Contract lifecycle primitives. Entitlement, Schedule, Condition, Allocation, ContractEvent. |
| `contracts` | Contract graph editor. Cytoscape-backed visual authoring of claims meshes. |
| `instruments` | Financial instruments. Escrow, forward, futures, options, vesting, staking, subscription, lease, amortization. |
| `bourse` | Peer-to-peer OTC asset exchange desk. |
| `bazaar` | Full e-commerce marketplace. Shops, products, cart → order → ledger payment, coupons, custodian gating. |
| `detections` | Incident and threat detection registry. |
| `logistics` | Physical package and transport tracking. Integrates with bazaar shipments. |
| `inventory` | Real-world object registry and tokenization anchor. |

### Knowledge & Learning
| App | Purpose |
|---|---|
| `verbena` | Abstract content bases. `AbstractTag`, `AbstractPage`, `AbstractSection` — inherited by all content apps. |
| `palimpsest` | Multi-author wiki. Collaborative pages with distributed authorship. |
| `memo` | Flashcard decks and SVG diagrams. Used as lecture material in academy. |
| `quizzes` | Trait-scoring quiz engine. Produces personality/skill profiles, not right/wrong grades. |
| `competence` | Skills registry. SkillBadge, SkillGroup, prerequisites. |
| `academy` | Full LMS. Courses → modules → lessons, student enrollment, certificates, cohorts, learning paths. |
| `library` | Media and reference library. Books, articles, audio, video, collections. |
| `bento` | Idea management and concept mapping. IdeaBox graph with directional links. |

### Project Management
| App | Purpose |
|---|---|
| `kanban` | Project and task management with ledger-backed practitioner compensation. |
| `mobilization` | Upstream emergency command. Report → Event → EmergencyStatus pipeline. |
| `response` | Field operations. Deployment → Assignment → Intervention → EvacuationRoute. |

### Geography
| App | Purpose |
|---|---|
| `locations` | PostGIS geographic layer. Address, Territory, Zone, Route, MapLayer. |

### Studio Only
| App | Purpose |
|---|---|
| `enigma` | Real-time group chat over WebSockets. |
| `ravioli` | Sole Neo4j boundary (connection, Cypher, search, analysis). Owns per-object "Export to graph" (checksum diff + :HISTORICAL versions); graph shape + bulk projection in `sql_neo4j_sync`. |
| `texlab` | Async LaTeX compilation with live log streaming. |
| `mandragora` | Jupyter-style compute engine. Notebooks, cells, ZMQ kernel backend. |
| `workflows` | DAG-based automation engine. Lambda/split/join/report nodes. |
| `weather` | Weather ingestion and forecasting via workflow nodes. |
| `sketch` | Collaborative real-time whiteboard. |
| `ocr` | Document OCR pipeline. Image → transform chain → text lines. |
| `steven` | AI agent management. AgentConnector, profiles, tools, conversation history. |
| `travels` | Route journeys and visit history. |

### Simulation (separate project)
| App | Purpose |
|---|---|

---

## Key Relations

```
Federation
  └── many Community (socialhub)
        ├── AssemblyConfig / Senate
        ├── AssemblyProposal → AssemblyDecision (hash-chain)
        │     └── enacts CommunityRule / CommunityTransactionFee / PollTax
        ├── many Person (members)
        │     ├── User (auth)
        │     ├── UserStrongbox (gervazy) → VaultMasterKey → DEK → EncryptedSecrets
        │     └── LedgerAccount (assets)
        └── many Shop (bazaar)
              └── Vendor → Product → Cart → Order → Payment → LedgerTransaction

Asset (assets)
  ├── AssetHolding → LedgerAccount
  ├── LedgerTransaction → LedgerEntry → LedgerHash (tamper-evident chain)
  ├── Obligation (debt between accounts)
  └── Contract (Lapis YAML) → Agreement

Person
  ├── Practitioner (kanban) → Task → Mission → Project
  ├── Teacher / Student (academy) → Course → Lesson (MemoDeck)
  ├── Participant (enigma) → Room (WebSocket)
  └── TribunalParty → TribunalCase → JurySession → TribunalRuling

ravioli (Neo4j)
  ← GraphChangeEvent (emitted by signals across all apps)
  → Neo4j nodes + relationships
```



========================================================================
========================================================================



========================================================================
  APP: toto.api
========================================================================

# toto.api

Outbound API connector registry and email delivery. Stores configuration for external API integrations; credentials are never stored here — they live in `gervazy`.

## Purpose

When toto needs to call an external service (webhook, LLM provider, SMTP relay), the operator registers an `ApiConnector` with the base URL and auth config. Secrets (API keys, SMTP passwords) are stored as `gervazy.EncryptedSecret` and referenced by FK — the connector config itself never contains plaintext secrets. `steven` subclasses `ApiConnector` for LLM providers. `EmailService` wraps SMTP config for community notification emails.

## Models

- `ApiConnector` — abstract base for outbound API connectors. Fields: `name`, `slug`, `base_url`, `auth_type` (`none` / `api_key_header` / `bearer_token` / `query_param`), `auth_config` (JSON schema-validated, rejects secret-like keys), `api_secret` (FK to `gervazy.EncryptedSecret`), `signing_key` (FK to `gervazy.EncryptedPrivateKey`), `owner` (FK to `auth.User`), `is_active`.
  - Validation rejects any `auth_config` key whose name looks like a secret (`api_key`, `token`, `password`, etc.) — enforces the pattern that secrets must go to gervazy.
- `Connector` — concrete subclass of `ApiConnector`. Adds: `description`, `tags`.
- `EmailService` — SMTP email sender configuration. Fields: `name`, `host`, `port`, `use_tls`, `username`, `smtp_secret` (FK to `gervazy.EncryptedSecret`), `from_email`, `is_active`, `owner`.

## Key coupling

- `gervazy.EncryptedSecret` — `api_secret` and `smtp_secret` are encrypted references, never plaintext.
- `steven.AgentConnector` subclasses `ApiConnector` to add LLM-specific fields (model, system prompt, temperature).
- `workflows` can trigger email delivery via an `EmailService` connector.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `gervazy` — EncryptedSecret and EncryptedPrivateKey for api_secret / signing_key────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.backup
========================================================================

# toto.backup

Platform backup signing and storage. Signs outbound backup packages with an RSA private key from gervazy and stores received backup archives.

## Purpose

The platform operator sets up a `BackupProfile` with a gervazy `EncryptedPrivateKey` as the signing key. Outbound backup archives are cryptographically signed before transmission; incoming archives are verified against the stored public key. `StoredBackup` records track each archive with a checksum and verification flag. This ensures backup integrity and authenticity across deployment boundaries.

## Models

- `BackupProfile` — signing key configuration for a platform. Fields: `platform` (OneToOne FK to `core.Platform`), `signing_key` (FK to `gervazy.EncryptedPrivateKey`, nullable), `verify_key` (public key PEM text for verifying incoming backups).

- `StoredBackup` — an archived backup file. Fields: `uid` (UUID), `platform` FK, `created_at`, `file` (Django `FileField` — stored at `stored-backups/{uid}/{filename}`), `file_size`, `checksum`, `is_verified` (bool — signature check passed), `notes`.

## Key coupling

- `core.Platform` — one backup profile per platform.
- `gervazy.EncryptedPrivateKey` — signing key decrypted at export time.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `core` — BackupProfile is OneToOne with core.Platform
- `gervazy` — EncryptedPrivateKey as archive signing key────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.bento
========================================================================

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


========================================================================
  APP: toto.connectors
========================================================================

# toto.connectors — external APIs → Bento-validated graph patches

Configure a **DataConnector** (which API, how to extract records, how records
map onto Bento categories/edge types) → run it (manually or on a schedule) →
every run produces an **ingestor proposal** reviewed in the Cytoscape diff UI
→ apply writes through `bento.graph_service`. Human-auditable end to end.

Surfaced as the **Connectors** tab inside Ravioli (`/connectors/`, superuser-only).

## Constraints honored

- **Neo4j is the source of truth.** A run's output is a *patch* (an
  `ingestor.IngestProposal`), never graph data.
- **Ravioli owns Neo4j.** Reads go through the ingestor catalog →
  `bento.graph_service`; writes only through `ingestor.services.apply`. This
  app opens **no** Neo4j connection of its own.
- **No LLMs. Deterministic only.** Declarative dot-path mapping + rapidfuzz
  dedupe against the live graph.
- **Secrets never live here.** Auth config/credentials belong to the
  `api.Connector` (Gervazy `EncryptedSecret` in the shared system strongbox);
  archived payloads and request logs are composed pre-auth.

## Anatomy of a run (`services/runner.execute_run`)

1. **extract** — `services/extract/rest.RestApiExtractor` fetches pages via
   `toto.api.client.execute_api_request` (auth injection, SSRF host allowlist,
   1 MiB/30 s caps; pagination: `none | page | offset | cursor`).
2. **archive** — all pages (bodies included) land as a JSON `VaultFile` in the
   `connectors-archive` bucket; an auth-free `request_log` goes on the run.
3. **transform** — `services/transform.py` maps records through the
   `mapping_spec`: within-run dedupe by (category, normalized identifier),
   graph dedupe via the ingestor catalog + rapidfuzz (exact match → an
   `existing` reference; near match → `duplicate_warning`), Bento-constrained
   relationships, and a skip of existing→existing edges already in the graph
   (`create_edge` is a Cypher CREATE — re-runs must not multiply edges).
4. **persist** — `ingestor persist_review` → proposal `ready`, run `review`.
5. **trusted auto-apply** (optional) — `ingestor.services.approval.
   approve_all_valid` (never approves validation errors) + `ingestor.services.
   apply.run` (idempotent/resumable); run ends `applied` with the full audit
   trail intact.

Run statuses: `pending → running → review | applied | empty | failed`. A human
apply through the ingestor UI flips `review → applied` via a post_save signal
on `IngestProposal`.

## `mapping_spec`

```jsonc
{
  "version": 1,
  "nodes": [{
    "rule_id": "author",                            // unique [a-z0-9_-]+
    "category_slug": "person",                      // existing BentoCategory
    "identifier": {"path": "author.display_name"},  // display + dedupe surface
    "properties": {                                 // each value EXACTLY ONE of:
      "name":   {"path": "author.display_name"},    //   dot-path ("ids.0.value" indexes lists)
      "source": {"const": "openalex"},              //   literal
      "note":   {"template": "{title} ({publication_year})"}  // "{path}" placeholders
    },
    "when": {"path": "author.display_name", "op": "truthy"}   // truthy|eq|ne|contains
  }],
  "relationships": [{
    "rule_id": "wrote",
    "edge_type_slug": "wrote",              // endpoints checked vs allowed_sources/targets
    "from_rule": "author", "to_rule": "work",  // both must fire on the same record
    "properties": {"year": {"path": "publication_year"}}
  }]
}
```

The category's identifier property (`searchable_property_names[0]`) is filled
from the identifier surface when unmapped, so required fields never come out
empty. Specs are validated structurally *and* against the live Bento templates
(`services/mapping.validate_mapping_spec`) on save and via the Validate button.

## `extract_config` (kind `rest_api`)

```jsonc
{
  "endpoint": "works",                 // relative to http_connector.base_url
  "method": "GET",                     // GET | POST
  "params": {"filter": "is_oa:true"},  // static, non-secret
  "headers": {},
  "records_path": "results",           // dot-path to the list; "" = body itself
  "pagination": {"strategy": "page", "param": "page", "start": 1, "max_pages": 10},
  "max_records": 1000,
  "timeout_seconds": 30
}
```

## Scheduling

`schedule_enabled` + `interval_minutes` per connector. A single global beat
task (`toto.connectors.tasks.connectors_scan_schedules`, every
`CONNECTORS_SCAN_MINUTES` min) claims due connectors under a row lock —
`next_run_at` advances before dispatch, in-flight runs are never stacked, and
an untrusted connector with a proposal still awaiting review is skipped (no
duplicate-proposal pileup). Runs stuck pending/running beyond
`STALE_RUN_MAX_AGE` (2 h — a killed worker) stop blocking their connector.
Deployments get a dedicated `celery_beat` container (see deploy.py).

## Deploy / flags

- `BUILD_CONNECTORS=1` (implies `BUILD_GRAPH`) — INSTALLED_APPS, `/connectors/`
  URLs, the Ravioli tab, the beat entry and ingress registration all follow it.
- Authenticated connectors need the shared system strongbox:
  `SABBIA_VAULT_PASSWORD` set (web **and** worker) + `manage.py
  connectors_init_vault` (ingress does this when the password is present).
  API keys are entered on the `api.Connector` admin form, never in configs.
- `manage.py ingress_connectors --full` seeds a no-auth **OpenAlex demo**
  mapped onto the seeded `note`/`concept` templates.

## Known limitations (v1)

- Property **updates** on existing nodes are out of scope: an exact-matched
  node becomes a reference with `properties: {}`.
- No HTTP retry/backoff/rate limiting (matches `toto.api.client`); a flaky
  upstream fails the run visibly and the next scheduled tick retries.
- The graph catalog is rebuilt per run and capped at
  `INGESTOR_CATALOG_MAX_NODES`; truncation is surfaced in run stats.

## Tests

```
cd portal && BUILD_NEO4J=1 BUILD_CONNECTORS=1 python manage.py test toto.connectors toto.ingestor
```

No live Neo4j or network required: HTTP is patched at `execute_api_request`,
graph writes at `graph_service.create_node/create_edge`, catalog at
`build_catalog` — the same seams the ingestor suite uses.


========================================================================
  APP: toto.core
========================================================================

# toto.core

Platform configuration and tenant identity. Defines the top-level `Platform` singleton, the `Federation` it belongs to, and the visual/branding system (`Theme`, `ColorMix`, `Font`).

## Purpose

`core` is the first app that boots. The `Platform` singleton defines this deployment's domain, name, and branding. It is injected into every request by `PlatformMiddleware` so templates render the correct theme without per-view DB queries. `Federation` groups communities into a named network. `DomainEntity` is the abstract base inherited by most domain models — it provides `uuid`, `slug`, `name`, `description`, `logo`, `metadata`, `created_at`, `updated_at` without additional tables.

## Models

- `Font` — a named typeface reference (family name + CSS import URL). Used by `Theme`.
- `ColorMix` — a named palette record (primary, secondary, accent, background, surface, text, border hex values). Used by `Theme`.
- `Theme` — visual identity for a platform or community. Links a `ColorMix` and two `Font` objects (body/heading). Has a `dark_mode` flag and a `custom_css` override field.
- `Federation` — a named grouping of platforms. Extends `DomainEntity` (slug, description, logo, metadata). One-to-one with a `Theme`.
- `Platform` — the singleton record representing this deployment. Fields: `name`, `slug`, `federation` (FK to `Federation`), `domain`, `contact_email`, `theme` (FK to `Theme`), `is_active`. One-to-one back-ref from `backup.BackupProfile`.

`DomainEntity` is the abstract base used by almost every domain model in the system. It provides: `uuid` (auto), `slug` (auto from name), `name`, `description`, `logo`, `metadata` (JSON), `created_at`, `updated_at`.

## Template tags

- `graph_export` — `{% load graph_export %}{% export_to_graph_button obj %}` renders a per-object "Export to graph" button on detail pages, linking to ravioli's export **preview** page. It lives in `core` (always installed) so templates can load it even in builds without the Neo4j layer; it renders nothing unless `RAVIOLI_ENABLED` is set and the object's model is graph-mapped (and not in `RAVIOLI_EXPORT_EXCLUDED_APPS`). The graph apps are imported lazily.

## Key coupling

- `core.Platform` is read at boot time by `sso_master.services.get_active_platform()` to resolve the OIDC issuer URL.
- `backup.BackupProfile` has a one-to-one with `Platform`.
- `Theme` is read by every template that renders the platform's branding.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.events
========================================================================

# toto.events

Scheduled events and personal availability calendar. Provides the event infrastructure used by mobilization for civilian emergency events.

## Purpose

Community organizers create `ScheduledEvent` records with a venue address, start/end time, and capacity. Members receive `EventInvite` records and RSVP. Personal `Availability` windows let members signal when they are free or busy for scheduling coordination. `EventBase` is also the abstract parent of `detections.Detection` — detections are time-anchored events with the same core fields.

## Models

- `EventCategory` — extends `DomainEntity`. Hierarchical category (self-referential parent).

- `EventBase` — abstract base (extends `DomainEntity`). Common fields: `category`, `title`, `description`, `starts_at`, `ends_at`, `is_public`, `is_cancelled`. Inherited by `ScheduledEvent` and also by `detections.Detection` (detections are time-anchored events).

- `ScheduledEvent` — a concrete event. Fields: `owner` (FK to `people.Person`), `organizers` (M2M to `people.Person`), `address` (FK to `locations.Address`), `max_participants` (nullable), `community` (FK to `socialhub.Community`), `registration_open`, `metadata`.

- `EventInvite` — an invitation from an event to a person. Fields: `event`, `person`, `status` (`pending / accepted / declined`), `sent_at`, `responded_at`.

- `Availability` — a person's availability window. Fields: `person` (FK to `people.Person`), `starts_at`, `ends_at`, `is_available` (bool; `False` = blocking), `recurrence` (JSON for repeating slots), `notes`.

## Key coupling

- `mobilization.MobilizationEvent.scheduled_event` — a mobilization event can be anchored to a calendar event.
- `EventBase` is the abstract parent of `detections.Detection`, so detections carry the same temporal fields.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `locations` — Event venue is an Address FK
- `people` — EventInvite invitee and Availability person────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.formica
========================================================================

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


========================================================================
  APP: toto.forum
========================================================================

# Forum — Real-time Chat

Discord-style chat: persistent channels, permanent readable history, full-text search, over
Django Channels WebSockets plus a JSON API.

Transport security is **TLS and nothing else**. Messages are stored as plaintext rows, which
is what makes durable, paginated, searchable history possible — a member who joins today can
read everything said before they arrived, and the server can run a text query over it.

> Renamed from **telegraph** and stripped of three cryptographic schemes (gervazy at-rest
> message encryption, client-side "secure-on-send" E2E, and an MLS relay over a prebuilt
> rotor WASM bundle) plus a 24-hour message TTL. Those existed to support a Signal-style
> privacy story; they cost a deployment secret whose loss made history unrecoverable, capped
> history at one day, and made message bodies impossible to query.

## Data model ([models.py](models.py))

- `ForumChannel` — name, slug, creator, and its members.
- `ForumMember` — the **only** membership record. A parallel `participants` M2M used to exist
  alongside it; the two disagreed, and a user dropped from one but not the other could still
  post over a raw websocket. See [permissions.py](permissions.py).
- `ForumMessage` — a plaintext message: `body`, optional `attachment` (a real file on disk,
  not a base64 `data:` URL), `reply_to`, `edited_at`, `deleted_at` (soft delete, so replies
  keep their anchor). Never expires — retention is deferred work, see
  [forum_todo.md](forum_todo.md).

**Attachments are not under `MEDIA_ROOT`.** nginx serves `/media/` unauthenticated with a
30-day cache, which would leave a private channel's images readable forever by anyone who
ever saw the URL — including a member who has since left. They live under
`settings.FORUM_ATTACHMENT_ROOT` (default: a `forum_attachments/` sibling of `MEDIA_ROOT`)
and are handed out only by `MessageAttachmentApiView`, which applies the same membership
check as the message they belong to.

## Permissions ([permissions.py](permissions.py))

Every entry point routes through one module:

| Who | May |
|-----|-----|
| anonymous | nothing — not even the channel list |
| signed in | browse channels, create one, join one |
| active member | read history, search, post, edit/delete their own messages |

Rosters, message history, search results and attachments are all scoped to active
membership. That scoping is not optional: messages are permanent now, so a leak here is
durable.

Membership is re-checked on live sockets too. A member removed via the REST/HTML leave
endpoints or the Django admin does not have their tab closed by any of those paths, so
`signals.py` broadcasts a control frame on every `ForumMember` write and the consumer
re-checks and disconnects. (Those endpoints deactivate per instance rather than with a
queryset `.update()`, because only instance saves fire the signal.)

## Search ([search.py](search.py))

Dual-backend, chosen at query time by `connection.vendor`:

- **postgresql** — `SearchVector`/`SearchQuery`/`SearchRank`, computed per query.
- **anything else** — case-insensitive substring matching.

Deliberately no `SearchVectorField` and no `GinIndex` in the migration: every host runs
**SpatiaLite in dev and in every clean-env gate, PostGIS in deployment**, and Postgres-only
DDL in `Meta.indexes` would break `manage.py migrate` on the SQLite side. The UI surfaces
which engine actually ran. Promoting to a stored, indexed vector is tracked in
[forum_todo.md](forum_todo.md).

## History ([store.py](store.py))

A freshly-connected socket receives the newest 50 messages. Older pages are fetched on
demand through
`GET /forum/api/channels/<slug>/messages/?before=<iso8601>&before_id=<uuid>&limit=`.

The cursor is the **`(created_at, id)` pair** of the oldest message you already hold, not
the timestamp alone: `created_at` is not a total order, so a timestamp-only cursor drops a
message whenever two share a timestamp across a page boundary, and the sort itself is
unstable.

## API Endpoints

| Method | URL | Description |
|--------|-----|-------------|
| GET · POST | `/forum/api/channels/` | List channels · create one |
| GET | `/forum/api/channels/{slug}/` | Channel detail + members (roster is members-only) |
| GET | `/forum/api/channels/{slug}/messages/` | Paginated history (`?before=&limit=`) |
| POST | `/forum/api/channels/{slug}/join/` · `/leave/` · `/leave-all/` | Membership |
| POST | `/forum/api/channels/{slug}/upload/` · `/upload-audio/` | Image / voice attachment |
| GET | `/forum/api/search/` | Message search (`?q=&channel=`) |
| GET | `/forum/api/messages/{uuid}/attachment/` | Membership-checked attachment download |

Auth and identity endpoints (`login`, `logout`, `me`, `me/mesh`, `health`, `apps`) are **not
here** — they were never chat. They live in [toto.api](../../../../toto-base/src/toto/api/)
and are mounted at `/api/`, with a legacy `/telegraph/api/` alias for a shipped enigma
desktop binary that cannot be updated in lockstep with the server.

## WebSocket Message Types

`ws/forum/<channel_slug>/`

| Type | Direction | Description |
|------|-----------|-------------|
| `chat_message` | both | A message (optionally with `reply_to`) |
| `image_message` / `voice_message` | server→client | Broadcast of an uploaded attachment |
| `chat_history` | server→client | The newest page, replayed on connect, plus `has_more` |
| `message_edit` / `message_delete` | both | Author-only mutation of an existing message |
| `typing_start` / `typing_stop` | both | Presence only, never persisted, never echoed to the sender |
| `room_participants` | server→client | Roster plus `online` — who actually has a socket open ([presence.py](presence.py)), which drives the presence dots |
| `membership_changed` | internal | Control frame from [signals.py](signals.py); makes a consumer re-check membership and disconnect if revoked |
| `system_error` | server→client | Error notification |

## Operations

Nothing to provision — no vault, no secret, no build artifact. The app needs a Redis channel
layer (for websockets and for the membership-revocation control frame), a cache (presence),
and a writable `FORUM_ATTACHMENT_ROOT`.

**Messages are never deleted.** There is no purge job at all; see
[forum_todo.md](forum_todo.md) for the retention work that replaces the old TTL.

## Testing

```bash
cd zenobia && .venv_test/bin/python manage.py test \
  toto.forum.tests.test_api_views toto.forum.tests.test_consumers \
  toto.forum.tests.test_history toto.forum.tests.test_models toto.forum.tests.test_views
```

Name the test modules explicitly: `toto` is a PEP 420 namespace package, so
`manage.py test toto.forum` cannot be discovered by unittest.


========================================================================
  APP: toto.gervazy
========================================================================

# toto.gervazy

Encryption-at-rest vault and document signing service. Implements a three-tier AES-256-GCM key hierarchy (password → Argon2id KDF → UKEK → wrapped VMK → wrapped DEK → encrypted objects) and an Ed25519-based cryptographic signing layer for contracts and documents.

## Purpose

When a user sets their vault password, Argon2id derives a UKEK from it (never stored). The UKEK encrypts a `VaultMasterKey` blob. The VMK in turn wraps `WrappedDataKey` records (one per namespace). Data keys encrypt the actual secrets, files, and private keys. Decryption requires the user's password at runtime — the system cannot read stored secrets without it.

The signing layer (`signing.py`) stores each person's Ed25519 private key encrypted inside their strongbox. To sign a document, the caller opens a `GervazyCryptoSession` with the strongbox password, which decrypts the private key in memory, produces a signature, then discards the key. The public key is stored in plaintext and can verify signatures at any time without a password.

The `CryptoAuditLog` records every operation for compliance. The OIDC signing key (`sso_master`) and all outbound API credentials (`api`) live in gervazy.

## Key hierarchy

```
User password (never stored)
    │ Argon2id KDF
    ▼
UKEK (User Key Encryption Key — derived, never stored)
    │ AES-256-GCM unwrap
    ▼
VaultMasterKey (VMK — stored as encrypted blob in UserStrongbox)
    │ AES-256-GCM unwrap
    ▼
WrappedDataKey (DEK — one per namespace, stored encrypted)
    │ AES-256-GCM decrypt
    ▼
EncryptedSecret / EncryptedFile / EncryptedPrivateKey (stored as encrypted blobs)
```

## Models

- `UserStrongbox` (`UserVault`) — per-user container. Links `auth.User` to Argon2id KDF parameters and one or more `VaultMasterKey` records.
- `VaultMasterKey` — the VMK blob encrypted by the UKEK. Fields: `encrypted_vmk`, `nonce`, `state`, `version`.
- `WrappedDataKey` — namespace-scoped DEK wrapped under a VMK. Fields: `encrypted_dek`, `nonce`, `vmk` FK, `state`, `version`.
- `EncryptedSecret` — arbitrary key/value secret (API tokens, SMTP passwords, OAuth secrets). Encrypted by a DEK; includes `aad` for binding to context.
- `EncryptedFile` — metadata for a chunked AES-256-GCM encrypted file. Chunks stored in `EncryptedFileChunk`.
- `EncryptedFileChunk` — one sequential encrypted chunk of an `EncryptedFile`.
- `EncryptedPrivateKey` — RSA or Ed25519 private key stored encrypted by a DEK. `public_key_pem` stored in plaintext. Supported types: `Ed25519`, `RSA-2048`, `RSA-4096`.
- `PersonSigningKey` — links a `people.Person` to their active `EncryptedPrivateKey` used for document signing. Only one `is_active=True` record per person at a time; old keys are retired (not deleted) so existing signatures remain verifiable.
- `CryptoAuditLog` — append-only log of vault operations. Never stores plaintext or key material.

## Signing service (`signing.py`)

`SigningService` is a stateless helper class:

| Method | Description |
|---|---|
| `get_active_signing_key(person)` | Returns the active `PersonSigningKey` or `None` |
| `provision_signing_key(session, wrapped_key, person)` | Generates Ed25519 key pair, encrypts private key, retires old key |
| `sign_document(session, person, payload, *, wrapped_key=None)` | Signs `payload` bytes; provisions key first if needed. Returns `DocumentSignature` |
| `verify(person, payload, signature_b64)` | Verifies a base64 signature against all known public keys for the person. No password required |
| `canonical_contract_payload(contract, person, signed_at)` | Builds the deterministic UTF-8 payload used for contract signing |

### DocumentSignature dataclass

```python
@dataclass(frozen=True)
class DocumentSignature:
    payload: bytes          # canonical message that was signed
    signature_b64: str      # base64-encoded Ed25519 signature
    signing_key_id: str     # key_id of the EncryptedPrivateKey used
    public_key_pem: str     # public key for offline verification
```

### Canonical contract payload format

```
sign:contract
uuid:<contract.uuid>
name:<contract.name>
person:<person.pk>
at:<signed_at.isoformat()>
```

## Key coupling

- `sso_master.SSOSigningKey.encrypted_key` → `gervazy.EncryptedPrivateKey` — OIDC signing key.
- `backup.BackupProfile.signing_key` → `gervazy.EncryptedPrivateKey` — backup signing key.
- `api.ApiConnector.api_secret`, `api.EmailService.smtp_secret` → `gervazy.EncryptedSecret` — outbound API credentials.
- `contracts.ContractSignatory.signing_key` → `gervazy.EncryptedPrivateKey` — the key that produced the contract's cryptographic signature.
- `people.Person` ← `gervazy.PersonSigningKey` — each person's active Ed25519 signing identity.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` (for `PersonSigningKey.person` FK)
- Otherwise standalone.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.ingestor
========================================================================

# toto.ingestor — text → Bento-validated graph patch

Paste arbitrary text → get a **proposed graph update** (existing-node references,
proposed new nodes, proposed relationships, with confidence, evidence and
validation) → review it as a Cytoscape **graph diff** → edit/approve/reject →
**apply the approved patch** to Neo4j.

Surfaced as the **Ingestor** tab inside Ravioli (`/ingestor/`, superuser-only).

## Constraints honored

- **Neo4j is the source of truth.** The proposal is a *patch*, never graph data.
- **Bento defines the templates.** New nodes use existing `BentoCategory`s; new
  relationships use existing `BentoEdgeType`s and their endpoint constraints.
- **Ravioli owns Neo4j.** Reads go through `bento.graph_service` (→ Ravioli's
  `Neo4jClient`); writes go through `bento.graph_service.create_node/create_edge`.
  This app opens **no** Neo4j connection of its own.
- **No LLMs. Deterministic only.** spaCy `EntityRuler` + statistical NER (fixed
  weights, deterministic inference) + rapidfuzz string distance + rule-based,
  template-constrained relationship proposal.

## Pipeline (`services/`)

`pipeline.build_proposal_dict(text)`:

1. **catalog** — read existing nodes (via `graph_service.iter_nodes`) and turn
   each category's *searchable* properties + aliases into match entries.
2. **detection** — spaCy: an `EntityRuler` built from the catalog detects
   *existing* nodes (pattern id = uid, label = category slug); the model's NER
   suggests *new* entities, mapped to categories via `INGESTOR_SPACY_LABEL_MAP`.
   Degrades to a blank pipeline (no model) — existing-entity detection + sentence
   segmentation still work.
3. **matching** — rapidfuzz (difflib fallback) flags duplicates / merge
   candidates of new entities against the catalog.
4. **relations** — for each sentence, ordered entity pairs yield only edges
   `graph_service.edge_types_between` allows; `BentoEdgeType.trigger_lemmas`
   gate/boost edges that need a verb cue.
5. **scoring** — deterministic confidence formulas (`services/scoring.py`).
6. **validation** — every node/edge checked against the live Bento template via
   `graph_service.validate_node` / `validate_edge` (also re-run at apply time).
7. **proposal** — assembled into the editable patch JSON (see below).

`apply.run(proposal_model)` writes approved + valid elements through Bento.
It is **idempotent/resumable**: created uids and applied relationship ids are
recorded in `IngestProposal.apply_result`, so a retry after a partial failure
never duplicates work.

## Proposal JSON (`IngestProposal.proposal`)

```jsonc
{
  "nodes": [{
    "temp_id": "n1", "kind": "new" | "existing",
    "category_slug": "person", "uid": null, "display": "Ada Lovelace",
    "properties": {"name": "Ada Lovelace"},
    "evidence": [{"text": "...", "start": 0, "end": 12, "sentence": 0}],
    "confidence": 0.55,
    "match": {"method": "fuzzy", "matched_uid": "...", "score": 0.91, "candidates": [...]},
    "duplicate_warning": false,
    "validation": {"status": "ok" | "error", "errors": []},
    "approval": "pending" | "approved" | "rejected",
    "merge_into_uid": null
  }],
  "relationships": [{
    "temp_id": "r1", "edge_type_slug": "works-at", "rel_type": "WORKS_AT",
    "from": "n1", "to": "n2", "properties": {},
    "evidence": [{"text": "...", "sentence": 0}],
    "confidence": 0.75, "trigger_matched": true,
    "validation": {"status": "ok", "errors": []},
    "approval": "pending"
  }]
}
```

Nodes/relationships reference each other by `temp_id`. Editing (category, props,
merge, approval, edge type) re-validates the element server-side.

## Bento template fields used

- `BentoCategory.searchable_properties` (list) — properties that identify a node
  in text; falls back to name/title/label. `alias_property` — node property
  holding alternate surface forms (list or comma string).
- `BentoEdgeType.trigger_lemmas` (list) — lemmas that propose/boost this edge.

## Settings knobs (`services/config.py`)

`INGESTOR_SPACY_MODEL` (default `en_core_web_sm`), `INGESTOR_SPACY_LABEL_MAP`,
`INGESTOR_CATALOG_MAX_NODES` (20000), `INGESTOR_DUP_THRESHOLD` (0.90),
`INGESTOR_CANDIDATE_THRESHOLD` (0.70), `INGESTOR_FUZZY_MAX_CANDIDATES` (5000).

## Deploy

`spacy` and `rapidfuzz` are in `requirements.studio.txt`. The NER model must also
be installed for new-entity suggestions:

```
python -m spacy download en_core_web_sm
```

Without it the Ingestor still runs (existing-entity detection + relationships),
but won't suggest uncategorized new entities — the review banner says so.

## Tests

`toto/toto/ingestor/tests/` — run from `portal/`:

```
BUILD_NEO4J=1 python manage.py test toto.ingestor
```

They cover Bento validators, matching, scoring, relations, proposal assembly,
the apply path (Neo4j mocked) and view permissions/flow — no live Neo4j or spaCy
model required.


========================================================================
  APP: toto.ingress
========================================================================

# toto.ingress

Base class for seed/ingress management commands. Not an app with models — provides `IngressCommand`, the common base for all `python manage.py ingress_*` commands.

## What it provides

- `IngressCommand(BaseCommand)` — abstract base. Provides:
  - `DATA_ROOT` — resolved path to the `data/` directory at repo root
  - `read_text(*parts)` — reads a text file from `DATA_ROOT`
  - `read_json(*parts)` — reads and parses a JSON file from `DATA_ROOT`
  - `--full` flag — subcommands check `self.full` to decide whether to run extended seeding
  - `process()` — abstract; subclasses implement this

## Usage

Each app's ingress command subclasses `IngressCommand`:

```python
from toto.ingress.management.commands import IngressCommand

class Command(IngressCommand):
    def process(self):
        # seed data here
        ...
```

Run with: `python manage.py ingress_<app_name> [--full]`

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.kanban
========================================================================

# toto.kanban

Project management with ledger-backed compensation. Projects contain missions → sprints → tasks, with practitioner roles and allowance-based income tied to `assets.LedgerAccount`.

## Purpose

A community creates a `Project`, adds `Practitioner` members with roles, and breaks work into `Campaign` → `Mission` → `Task` chains across `Sprint` iterations. Practitioners earn compensation through `PractitionerAllowance` records — daily, hourly, or fixed amounts paid from a ledger account — automatically posted by Celery beat at 17:00 on weekdays. Projects can be tokenized on the ledger as shares. Emergency response operations overlay kanban campaigns and missions for field command structure.

## Models

- `Project` — extends `DomainEntity`. Top-level container. FK to `socialhub.Community`. Has `is_public`, `is_archived`.
- `Column` — a kanban board state column inside a project (`Backlog`, `In Progress`, `Done`, etc.). Fields: `project`, `name`, `order`, `is_done_column`, `auditors` (M2M to `Practitioner` — who may move tasks here).
- `Campaign` — a named initiative within a project. FK to `Project`. Used as an overlay by `mobilization.MobilizationEvent`.
- `Mission` — a goal within a campaign. Fields: `campaign`, `title`, `is_complete`. Deployments in the `response` app can optionally link to a `Mission`.
- `Sprint` — a time-boxed iteration. Fields: `project`, `name`, `starts_at`, `ends_at`, `is_active`.
- `Practitioner` — a `Person`'s role in a project. Fields: `project`, `person` (FK to `people.Person`), `role` (`contributor / reviewer / auditor / manager / observer`), `default_income_account` (FK to `assets.LedgerAccount`), `joined_at`.
- `ProjectCommitment` — practitioner commitment record. Fields: `practitioner`, `hours_per_day`, `starts_at`, `ends_at`.
- `Task` — a unit of work. Fields: `column`, `sprint`, `assigned_to` (FK to `Practitioner`), `reviewer`, `title`, `description`, `weight` (Fibonacci: 1/2/3/5/8), `completed_at`. M2M to `Practitioner` (collaborators).
- `PractitionerAllowance` — recurring or fixed compensation rule. Fields: `practitioner`, `payer_account` (FK to `assets.LedgerAccount`), `recipient_account`, `allowance_type` (`per_diem / hourly / fixed / travel / meal / other`), `amount_base_units`, `asset`, `is_active`, `starts_at`, `ends_at`.
- `DocumentationPage` / `DocumentationSection` — rich documentation on a mission. `DocumentationPage` can be marked `is_manual`.
- `ProjectTokenization` — links a `Project` one-to-one to an `assets.Asset`. Immutable; delete is blocked. Represents project equity on the ledger.

## Celery beat task

`toto.kanban.tasks.pay_daily_allowances` runs weekdays at 17:00. For every active `PractitionerAllowance`, it creates an `assets.Obligation` against the payer account.

## Key coupling

- `assets.LedgerAccount` — income accounts for practitioners, payer accounts for allowances.
- `mobilization.MobilizationEvent.kanban_campaign` — events overlay a campaign board.
- `response.Deployment.kanban_mission` — deployments can be linked to a mission board.
- `detections.Detection.mitigation_task` — a task can be the designated mitigation for a detection.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — ProjectTokenization links project to Asset; task rewards
- `locations` — Project location / territory FK
- `people` — Practitioner is a Person; task assignments
- `verbena` — DocumentationPage extends AbstractPage

────────────────────────────────────────────────────────────────────────
## Enigma JSON API

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/kanban/api/projects/` | Projects where user is lead or committed practitioner |
| GET | `/kanban/api/projects/{id}/` | Project detail with columns |
| GET | `/kanban/api/projects/{id}/tasks/` | All tasks for a project |
| POST | `/kanban/api/projects/{id}/tasks/` | Create task `{title, column_id, description?}` |
| PATCH | `/kanban/api/tasks/{id}/` | Update task fields |
| POST | `/kanban/api/tasks/{id}/promote/` | Move task to next column |
| POST | `/kanban/api/tasks/{id}/demote/` | Move task to previous column |

Promote/demote returns 400 if the task is already at the first/last column.

### Testing
```bash
cd portal && python manage.py test toto.kanban.tests_api
```


========================================================================
  APP: toto.locations
========================================================================

# toto.locations

GIS-backed geographic model layer. All spatial data uses PostGIS SRID 4326 (WGS84). Models extend `DomainEntity`.

## Purpose

`locations` is the geographic substrate for the rest of the system. Addresses pin people, communities, shops, and events to points on the map. Zones scope delivery areas, emergency declarations, and kanban campaigns. Routes define logistic and evacuation paths. Map layers add thematic overlays (heat maps, polygon annotations) to the community map dashboard. PostGIS enables spatial queries — "all detections within this zone", "nearest inventory site to this address".

## Models

- `Address` — a postal + geographic point. Fields: `street`, `city`, `postal_code`, `country`, `point` (PostGIS `PointField`, nullable), `community` (FK to `socialhub.Community`).

- `Territory` — a large geographic area (country, region). Fields: `name`, `code`, `polygon` (PostGIS `MultiPolygonField`, nullable).

- `Zone` — a named sub-area within a community or territory. Fields: `name`, `community` FK, `territory` FK, `polygon` (PostGIS `PolygonField`, nullable), `zone_type` (e.g. `residential`, `commercial`, `industrial`, `emergency`).

- `RouteChain` — an ordered sequence of `Route` objects forming a multi-segment path. Fields: `name`, `routes` (M2M to `Route`).

- `Route` — a named spatial path. Fields: `name`, `start_address` / `end_address` (FKs to `Address`), `linestring` (PostGIS `LineStringField`, nullable), `route_type` (`road / rail / water / air / other`), `distance_km`, `estimated_duration_minutes`, `is_active`.

- `MapLayer` — a named data layer for map display. Fields: `name`, `layer_type` (`geojson / wms / tile`), `source_url`, `style_config` (JSON), `is_public`, `community` FK.

- `MapLayerPolygon` — a polygon feature within a map layer. Fields: `layer` FK, `name`, `polygon` (PostGIS `PolygonField`), `properties` (JSON).

## Key coupling

- `detections.Detection` — geographic anchors (`address`, `zone`, `route`)
- `mobilization.EmergencyStatus.zone` — emergency zones
- `response.EvacuationRoute`, `DeploymentRoute` — routes used in field operations
- `inventory.InventorySite.address` — site locations
- `people.Person.address` — person home address
- `events.ScheduledEvent.address` — event venue
- `travels.Visit.location` — visit destinations

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — Territory and Zone can have a Person administrator

────────────────────────────────────────────────────────────────────────
## Enigma JSON API

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/locations/api/zones/` | List zones with territory name |
| GET | `/locations/api/addresses/` | List addresses with lat/lng if geocoded |
| POST | `/locations/api/addresses/` | Create address `{street, building, locality_name, country_name}` (auth) |
| GET | `/locations/api/addresses/{id}/` | Address detail |

### Testing
```bash
cd portal && python manage.py test toto.locations.tests_api
```


========================================================================
  APP: toto.mandragora
========================================================================

# toto.mandragora

*(Studio only — requires BUILD_STUDIO=1)*

Jupyter-style compute kernels over WebSockets. Notebooks contain cells; execution is forwarded to a ZMQ kernel server process and results are streamed back.

## Models

- `ExecutableUnit` — abstract base for anything that can be executed. Fields: `source` (code text), `status` (`idle / queued / running / done / error`), `execution_count`, `last_run_at`, `output` (JSON — list of output objects per Jupyter msg spec).

- `ComputeKernel` — a named kernel session. Fields: `name`, `kernel_id` (UUID, the ZMQ kernel process ID), `language` (`python / r / julia`), `status` (`starting / idle / busy / dead`), `owner` (FK to `people.Person`), `created_at`, `last_activity_at`.

- `KernelDependency` — a Python package or system dependency required by a kernel. Fields: `kernel` FK, `package_name`, `version_spec` (e.g. `>=1.2`), `install_status` (`pending / installed / failed`).

- `Notebook` — a named collection of cells. Fields: `name`, `slug`, `kernel` (FK to `ComputeKernel`), `owner` (FK), `community` (FK, nullable), `is_public`, `created_at`.

- `Cell` — extends `ExecutableUnit`. One notebook cell. Fields: `notebook` FK, `cell_type` (`code / markdown / raw`), `order`.

## How it works

1. Client sends `execute` over WebSocket to the Channels consumer.
2. Consumer forwards the request to the kernel server at `KERNEL_SERVER_ADDR` (`tcp://kernel_server:5555`) via ZMQ.
3. The kernel server (standalone Python process) runs the code in a managed subprocess and streams outputs back.
4. Consumer relays outputs to the client and persists them on `ExecutableUnit.output`.

## Key coupling

- `KERNEL_SERVER_ADDR` — must point to the running ZMQ kernel server.
- `workflows.WorkflowNode` — workflow nodes of type `kernel_cell` can execute a `Cell`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.memo
========================================================================

# toto.memo

File-based **presentation** viewer and browser editor. Memo renders and edits
self-contained presentation `.pml` (Presentation Markup Language) files stored in
the vault — it has no database models of its own.

## How it works

A presentation is a single self-contained XML document
(`file_type="presentation"`) parsed by [`presentation_format.py`](presentation_format.py):

```xml
<?xml version="1.0" encoding="utf-8"?>
<presentation version="1" title="My Talk">
  <slide>
    <title>Welcome</title>
    <body><![CDATA[
      <p>HTML content</p>
      <img src="data:image/png;base64,...">      <!-- raster, embedded ≤640px -->
      <svg ...>...</svg>                          <!-- vector, inlined verbatim -->
    ]]></body>
  </slide>
</presentation>
```

The file is the single source of truth (same model as `.tpy` notebooks in
`toto.mandragora`): parsed when the viewer/editor opens, serialized back on Save.
Slide bodies are CDATA-wrapped so pasted HTML / `<img>` data URIs / inline
`<svg>` stay literal. Each `<slide>` becomes one reveal.js `<section>`.

## Entry points

- **Vault Play button** → `memo:present` — reveal.js slideshow viewer
  (registered via `plugins/vault_play_plugins.py`).
- **Vault Edit button** → `memo:source` — plain-text (Ace, XML mode) editor of
  the raw presentation XML; in the vault a presentation is just an editable
  text file (registered via `plugins/vault_editor_plugins.py`).
- `memo:edit` — the structured slide editor with client-side image resize
  (≤640px) + base64 embedding and SVG inlining. Reached from the memo app
  itself: the index cards, the player's Edit link, and the source editor's
  *Slides* button.
- `memo:save` / `memo:source_save` — persist slides / raw XML back to the
  vault file.
- `memo:index` — the presentation workspace: gallery of presentations the
  user can open, with one-click *New presentation* (`memo:create`). Linked
  from the dashboard as the **Presentations** card.

New presentations are created from the workspace's *New presentation* button
or the vault's *New File → presentation* menu (`vault.CreateEmptyFileView`
seeds a blank document and routes to the editor).

## Detection

The dedicated `.pml` extension is what marks a file as a presentation —
`VaultFile._EXT_MAP` maps `.pml` → `presentation` (extension-first, the same way
`.tpy` → `notebook`). Generic `.xml` files stay typed `xml`. `.pml` is plain XML
internally; the extension just disambiguates intent.

## Trust

The viewer renders slide bodies as raw HTML (`|safe`), the same trust model as
serving an uploaded `.html`/`.svg` vault file. Presentations are owner-authored
and viewing respects vault visibility.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `vault` — presentations are `VaultFile`s; play/editor plugins wire the buttons.
- reveal.js (`static/vendor/reveal/`) — slideshow rendering.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.ocr
========================================================================

# toto.ocr

*(Knowledge-Graph build — requires BUILD_NEO4J=1; needs the Tesseract binary + pytesseract)*

A small, stateless **OCR sub-tab inside the Knowledge Graph** (ravioli tab bar).
There are **no database models** — the flow is entirely request/response.

## Flow

1. **Load screenshot** — the user picks an image in the OCR tab.
2. **Run OCR** — `ocr:run` feeds the upload to `OcrHelper` (pytesseract) and returns the
   extracted text as JSON.
3. **Save to bucket (optional)** — a checkbox unfolds a bucket dropdown; when set, the
   screenshot is stored as an `image` `vault.VaultFile` in the chosen bucket.
4. **Ingest text** — the extracted (and editable) text is POSTed to `ingestor:generate`,
   then the user is taken to the resulting ingestor proposal.

## Pieces

- `ocr.py` — `OcrHelper`: the pytesseract wrapper (run + line grouping). The only engine.
- `views.py` — `ocr_home` (renders the tab) and `ocr_run` (Tesseract + optional vault save).
- `templates/ocr/ocr.html` — Alpine `ocrFlow()` UI; includes `ravioli/_tabs.html`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `vault` — optional save creates a `VaultFile` in a user-owned `Bucket`.
- `ingestor` — the "Ingest text" button reuses `ingestor:generate`.
- System: the `tesseract-ocr` binary + language packs, and the `pytesseract` package.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.people
========================================================================

# toto.people

Person profiles. The `Person` model is the shared identity anchor across every domain app. Every user who appears in a community, on a project, in the ledger, or in an emergency response first exists as a `Person`.

## Purpose

When a user registers or is imported, a `Person` record is created and linked one-to-one with their `auth.User`. After that, every domain action — joining a community, taking on a task, receiving a salary, casting an assembly vote, getting deployed as a responder — uses the `Person` FK, never the raw `User`. This decouples platform identity from Django's auth layer and lets the system represent people who have no login account (referenced contacts, external parties).

## Models

- `Person` — extends `DomainEntity`. Key fields:
  - `user` — OneToOne with Django `auth.User` (nullable; people without login accounts can exist, e.g. referenced contacts)
  - `communities` — M2M to `socialhub.Community` via a through table (direct M2M)
  - `patron` — self-referential FK (mentor / sponsor relationship)
  - `address` — FK to `locations.Address`
  - `is_federal_agent` — bool; gates responder eligibility in `mobilization`
  - `digital_signature` — TextField; base64-encoded PNG of the person's handwritten (canvas) signature, used as a decorative element in signed documents
  - `display_name`, `bio`, `avatar` — public profile fields
  - All `DomainEntity` fields: `uuid`, `slug`, `name`, `metadata`, `created_at`, `updated_at`

## Key coupling

Almost every model in the system FKs into `Person`:
- `mobilization.Responder` — one-to-one
- `kanban.Practitioner` — per-project role record
- `assembly.AssemblyVote`, `AssemblyProposal` — governance actors
- `socialhub.MembershipApplication`, `ReferenceRequest` — community onboarding
- `tribunal.TribunalParty` — case actors
- `academy.Teacher`, `Student` — LMS roles
- `gervazy.PersonSigningKey` — the person's active Ed25519 signing key (stored encrypted in their strongbox)
- `contracts.ContractSignatory` — records of contracts this person has been asked to sign

`is_federal_agent` and community membership (via `communities` M2M) are read by `mobilization.Responder.clean()` to enforce eligibility.

`digital_signature` stores a decorative handwritten signature (base64 PNG), separate from the cryptographic Ed25519 key managed by gervazy.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `locations` — Person.address FK to locations.Address────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.polls
========================================================================

# toto.polls

Lightweight ad-hoc polling. Simple polls with options and votes. Separate from the `assembly` governance system — intended for informal community temperature checks, not binding decisions.

## Purpose

A community member creates a `Poll` with a question and 2–N `Option` choices. Members vote; results are tabulated in real time. Polls close at `closes_at`. Unlike `assembly` proposals, polls produce no `AssemblyDecision` and have no governance effect — they are purely informational. Use `assembly` for binding votes on rules, fees, or taxes.

## Models

- `Poll` — a question put to a community or group. Fields: `community` (FK to `socialhub.Community`), `author` (FK to `people.Person`), `title`, `question`, `is_multiple_choice`, `status` (`open / closed`), `closes_at`, `is_public`, `created_at`.

- `Option` — one choice within a poll. Fields: `poll` FK, `text`, `order`.

- `Vote` — a person's selection. Fields: `poll`, `option`, `voter` (FK to `people.Person`), `cast_at`. Unique on `(poll, voter, option)` when multiple choice; unique on `(poll, voter)` otherwise (enforced at service layer).

## Key coupling

- Polls are community-scoped but otherwise standalone.
- For binding governance votes, use `assembly.AssemblyProposal` / `AssemblyVote`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.ravioli
========================================================================

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

The **History** tab (`history.html`) shows every kept `:_HISTORICAL` snapshot as a Cytoscape graph (canonical nodes + version chains), coloured by a **keep-depth** control: versions beyond the depth are flagged prunable (red). Clicking any node opens a property detail panel (same as the query view; `history_data` returns each node's `props`). **Prune history** opens the same review modal for the delete-only plan, keeping the N newest snapshots per node (default `RAVIOLI_DEFAULT_MAX_HISTORY`).

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

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone Neo4j boundary. (`sql_neo4j_sync`, `bento`, and `neo_editor` depend on it.)────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.sabbia
========================================================================

# sabbia

Headless agentic/chatbot **backend**. Hosts any number of chat agents; each agent
only chats with a user over websockets. No UI — the floating chat widget lives in
the separate `toto.steven` app.

## Pieces

- `models.py` — `Agent`, `AgentConnector` (encrypted creds via Gervazy), `Conversation`,
  `ChatMessage`, `PlatformChatbot` (links a `core.Platform` to the agent its widget shows).
- `endpoints/` — pluggable chat transports. Each implements `ChatEndpoint.chat(messages) -> str`.
  Add a type = new class + one line in `endpoints/__init__.py:REGISTRY`.
  - `openai` — OpenAI Chat Completions; key decrypted from the sabbia vault.
  - `ollama` — calls the `toto.vicuna` chat endpoint (which proxies to local Ollama).
- `consumers.py` / `routing.py` — `ws/sabbia/agent/<slug>/`, single-shot replies, logged-in only.
- `vault.py` + `sabbia_init_vault` — server-side Gervazy system strongbox unlocked by
  `SABBIA_VAULT_PASSWORD` (mirrors `toto.sso_master`'s signing vault).
- `ingress_sabbia` — seeds the Steven (OpenAI) agent + optional Ollama agent + PlatformChatbot.

## Deployment flags

- `BUILD_SABBIA` — install this backend (implies `BUILD_STUDIO` for websockets).
- `SABBIA_OPENAI` — enable the OpenAI endpoint + seed Steven (needs `OPENAI_API_KEY` to seed the key,
  `SABBIA_VAULT_PASSWORD` at runtime).
- `SABBIA_OLLAMA` — install `toto.vicuna` + enable the Ollama endpoint + seed an Ollama agent.

## Setup

```
SABBIA_VAULT_PASSWORD=... python manage.py sabbia_init_vault
OPENAI_API_KEY=sk-... python manage.py ingress_sabbia
```


========================================================================
  APP: toto.sketch
========================================================================

# toto.sketch

*(Studio only — requires BUILD_STUDIO=1)*

Collaborative real-time whiteboard over WebSockets. Users draw on a shared canvas; object state is broadcast via Django Channels.

## Models

- `Board` — a named whiteboard. Fields: `name`, `slug`, `community` (FK, nullable), `created_by` (FK to `people.Person`), `is_public`, `created_at`.

- `BoardObject` — a persistent canvas object. Fields: `board` FK, `object_id` (UUID, stable client-side ID), `object_type` (`shape / text / image / connector / sticky`), `data` (JSON — position, size, color, content), `z_index`, `created_by` (FK to `people.Person`), `updated_at`.

## WebSocket protocol

- Connect to `ws://.../ws/sketch/{board_slug}/`
- Channels consumer handles `object_create`, `object_update`, `object_delete` messages and broadcasts to all connected users.
- `BoardObject` records are persisted on each mutation so boards survive reconnects.

## Key coupling

- Requires Django Channels + Redis channel layer (studio mode).

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — Board creator and participant Person FKs
- `vault` — Board snapshots stored as VaultFile────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.socialhub
========================================================================

# toto.socialhub

Community identity and membership management. A `Community` is the primary grouping unit: it owns an economy, a governance system, a marketplace, and a responder pool.

## Purpose

A `Community` is the container around which everything else organizes. People join via `MembershipApplication` (email-verified, requires a reference from an existing member via `ReferenceRequest`). Once a member, they can participate in governance, trade in the shop, work on projects, and respond to emergencies. The `is_federal_tribe` flag elevates a community to federal status — its members become eligible as emergency responders. Communities form hierarchies (parent → child) and share a `Federation`.

## Models

- `Community` — extends `DomainEntity`. Key fields:
  - `federation` — FK to `core.Federation`
  - `org_type` — `guild / company / non_profit / family / other`
  - `is_autonomous` — bool; self-governing community with internal leadership
  - `is_federal_tribe` — bool; members are eligible as emergency responders and exempt from poll tax
  - `is_foreign` — bool; community outside local jurisdiction
  - `head` — FK to `people.Person` (community leader)
  - `senior_members` — M2M to `people.Person` (can manage news/announcements)
  - `location` — FK to `locations.Address`
  - `territory` — FK to `locations.Territory`
  - `parent` — FK to self (community hierarchy)
  - `email_service` — FK to `api.EmailService`

- `CommunityNewsTopic` — extends `AbstractTag`. Tag/category for news posts.

- `CommunityNewsPost` — extends `AbstractSection`. A rich-text news article inside a community. Fields: `community` (FK), `author` (FK to `people.Person`), `topics` (M2M to `CommunityNewsTopic`), `visibility` (`public / community`).

- `MembershipApplication` — an email-verified request to join a community. Fields: `email`, `community` (FK), `code` (6-digit verification), `verified_at`, `expires_at`.

- `ReferenceRequest` — a reference letter provided by an existing member for a `MembershipApplication`. Fields: `application` (FK to `MembershipApplication`), `referrer` (FK to `people.Person`), `message`, `status` (`pending / accepted / declined`), `responded_at`.

- `Constitution` — community founding document. Fields: `community` (FK), `content`, `version`, `adopted_at`.

- `ConstitutionSignature` — a person's signature on a constitution. Fields: `constitution` (FK), `person` (FK to `people.Person`), `signed_at`.

## Key coupling

- `Community` is referenced by nearly every domain model (kanban projects, bazaar shops, assembly proposals, mobilization events, emergency statuses, deployments).
- `Community.is_federal_tribe` gates `mobilization.Responder` eligibility.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `api` — EmailService FK for community notification emails
- `locations` — Community headquarters Address FK and Territory FK
- `people` — CommunityNewsPost author; MembershipApplication applicant

────────────────────────────────────────────────────────────────────────
## Enigma JSON API

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/socialhub/api/profiles/` | List people profiles (sorted by display_name) |
| GET | `/socialhub/api/profiles/{slug}/` | Profile detail with communities list |
| GET | `/socialhub/api/communities/` | List communities (sorted by name) |
| GET | `/socialhub/api/communities/{slug}/` | Community detail with senior members + latest news |

All endpoints are public (no auth required).

### Testing
```bash
cd portal && python manage.py test toto.socialhub.tests_api
```


========================================================================
  APP: toto.sql_neo4j_sync
========================================================================

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

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

`ravioli` (Neo4j connection). Reads — but does not import — the models of the apps it projects.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.sso_client
========================================================================

# toto.sso_client

OIDC consumer configuration. Stores the provider endpoint and client credentials imported from an `sso_master` connection bundle. Used by apps that authenticate users against an external toto SSO provider.

## Models

- `OIDCProviderConfig` — single-row config table (only one record should be active). Fields:
  - `label` — human name for the provider (default `"Portal"`)
  - `portal_url` — base URL of the SSO provider
  - `client_id`, `client_secret` — OIDC credentials (secret stored in plaintext here; consider moving to gervazy in production)
  - `scopes` — requested scopes (default `"openid email profile"`)
  - `app_name`, `trusted` — manifest export fields (what this app declared itself as)
  - `redirect_uris` — newline-separated allowed redirect URIs
  - `active`, `imported_at`
  - `redirect_uris_list()` — property returning a cleaned list

## Key coupling

- `sso_core.manifest.ConnectionBundle` — populated via admin import of a connection bundle JSON.
- `sso_master.SSORelyingParty` — the provider-side counterpart record.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `sso_core` — OIDCProviderConfig stores a ConnectionBundle imported from sso_master via sso_core dataclasses────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.sso_core
========================================================================

# toto.sso_core

Shared SSO manifest and connection bundle schemas. No models — pure Python dataclasses and utilities for inter-app SSO provisioning.

## What it contains

- `manifest.ManifestBundle` — a dataclass exported by an SSO consumer app. Declares what OIDC client it needs: `client_id`, `client_type`, `redirect_uris`, `scopes`, `trusted`.
- `manifest.ConnectionBundle` — a dataclass issued by the SSO provider (`sso_master`) to a consumer. Contains the full OIDC endpoint URLs, client credentials, and signing key public cert.
- `manifest.OIDCClientSpec` — nested spec inside `ManifestBundle`.

Both bundles serialize to/from JSON and have no Django dependency.

## Purpose

Enables the "import connection bundle" workflow: a consumer app exports a `ManifestBundle` JSON → operator imports it into `sso_master` admin → provider provisions an `SSORelyingParty` record and exports a `ConnectionBundle` → operator imports it into the consumer app (`sso_client`).

## Key coupling

- `sso_master` — reads `ManifestBundle` to provision `SSORelyingParty`.
- `sso_client.OIDCProviderConfig` — stores the imported `ConnectionBundle`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.sso_master
========================================================================

# toto.sso_master

Full OpenID Connect (OIDC) provider. Issues ID tokens, access tokens, and JWKS. Signs tokens with RSA keys stored encrypted in `gervazy`.

## Purpose

toto acts as its own identity provider. External apps and internal services register as OIDC clients. When a user logs into a client app, they are redirected to toto's `/sso/authorize/` endpoint, authenticate, and receive a JWT ID token signed with the active `SSOSigningKey`. The private signing key never leaves gervazy — `services.get_signing_private_key_pem()` unlocks it at token-issue time using `SSO_VAULT_PASSWORD`. The JWKS endpoint (`/sso/jwks/`) lets relying parties verify token signatures.

## Models

- `SSOClient` — a registered OIDC client application. Fields: `client_id` (unique), `client_secret` (hashed), `client_type` (`confidential / public`), `name`, `redirect_uris` (text, one per line), `scopes` (space-separated), `is_active`, `is_trusted` (trusted clients skip the consent screen), `metadata`.

- `SSOSubject` — links a Django `auth.User` to an OIDC `sub` claim (stable UUID per user per client). Fields: `user` FK, `client` FK, `sub` (UUID). Unique on `(user, client)`.

- `SSOAuthorizationCode` — a short-lived authorization code issued during the authorization code flow. Fields: `client`, `user`, `code` (unique), `redirect_uri`, `scope`, `nonce`, `code_challenge` / `code_challenge_method` (PKCE), `expires_at`, `is_used`.

- `SSOSigningKey` — an RSA key pair used to sign JWTs. Fields: `key_id` (UUID `kid` claim), `algorithm` (default `RS256`), `public_key_pem` (plaintext), `encrypted_key` (FK to `gervazy.EncryptedPrivateKey`), `is_active`, `created_at`. Only one key is active at a time.

- `SSOAccessToken` — an issued access token (stored for introspection). Fields: `client`, `user`, `token` (hashed), `scope`, `expires_at`, `is_revoked`, `issued_at`.

- `SSORelyingParty` — extends `SSOClient`. An explicitly provisioned relying party (an external client deployment). Adds: `display_name`, `description`, `connection_bundle_hash` (SHA-256 of the imported connection bundle).

## Services (`services.py`)

| Function | What it does |
|---|---|
| `get_active_platform()` | Reads `core.Platform` singleton for issuer domain |
| `get_issuer(request)` | Returns the OIDC issuer URL (`https://{domain}`) |
| `get_active_signing_key()` | Returns the active `SSOSigningKey` |
| `get_signing_private_key_pem()` | Unlocks the gervazy vault and returns the RSA PEM |
| `get_jwks()` | Builds the JWKS endpoint response dict |
| `build_id_token(request, ...)` | Signs and returns a JWT ID token |
| `get_user_claims(user, scopes)` | Returns claims dict for the requested scopes |
| `verify_pkce(verifier, challenge, method)` | Validates PKCE code verifier against challenge |

## Key coupling

- `gervazy.EncryptedPrivateKey` — signing key decrypted at token-issue time using `SSO_VAULT_PASSWORD`.
- `core.Platform` — issuer URL is derived from platform domain.
- `sso_core.manifest.ConnectionBundle` — used to provision `SSORelyingParty` records for connected apps.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `gervazy` — SSOSigningKey wraps an EncryptedPrivateKey for token signing
- `sso_core` — ConnectionBundle exported as sso_core dataclass for client import────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.steven
========================================================================

# steven

The chat-widget **UI** app. A thin shell — no models, urls, or views.

Injects a hovering "Ask AI" chat window site-wide via a `FloatingPlugin`
(`plugins/floating_plugins.py`), rendered by `oya/base.html`'s
`{% render_floating_plugins %}`. The widget opens a websocket to the headless
`toto.sabbia` backend (`ws/sabbia/agent/<slug>/`) and chats with the agent that
`sabbia.PlatformChatbot` links to the active platform.

- Shown only to **logged-in** users (chat requires an authenticated socket).
- Renders nothing when sabbia isn't installed or no PlatformChatbot is configured.

## Deployment flag

`BUILD_STEVEN` installs this app and implies `BUILD_SABBIA` (the backend).


========================================================================
  APP: toto.texlab
========================================================================

# toto.texlab

*(Requires BUILD_LATEX=1 — installed together with `toto.texplay`)*

File-based **LaTeX workspace**. Documents are plain `.tex` vault files
(`file_type="latex"`, plus `.bib` bibliographies) — the vault file is the
single source of truth, the same model as `.pml` presentations (`toto.memo`)
and `.tpy` notebooks (`toto.mandragora`). The old `LatexWorkspace`/`LatexFile`
models were dropped in migration `0002_remove_workspace_models`; only
`CompileRun` (per-file compile audit) remains in the database.

## Entry points

- `texlab:index` — the LaTeX workspace: lists `.tex`/`.bib` vault files the
  user can open, with one-click *New document* (`texlab:create`). Linked from
  the dashboard as the **LaTeX** card.
- **Vault Edit button** → `texlab:file_display` — Ace editor (latex mode) with
  Save, compile trigger and compile history
  (registered via `plugins/vault_editor_plugins.py`).
- **Vault Play button** → `texplay:latex_play` — compile-and-preview the PDF
  (registered via `toto.texplay.plugins.vault_play_plugins`).
- `texlab:compile_latex` / `texlab:compile_status` — start a compile
  (Celery/workflow-backed when available, synchronous fallback otherwise) and
  poll its `CompileRun`.

New documents are created from the workspace's *New document* button (seeds
`BLANK_TEX_DOCUMENT`) or the vault's *New File → latex* menu
(`vault.CreateEmptyFileView` mirrors the same blank skeleton).

## Detection

`VaultFile._EXT_MAP` maps `.tex`/`.sty`/`.cls`/`.dtx`/`.ins` → `latex` and
`.bib` → `bib` (extension-first). The files stay plain text and are editable
as such.

## Compilation

`compile.py` runs `pdflatex -interaction=nonstopmode` in a tempdir over the
file (plus sibling bucket files), storing the output PDF as a `VaultFile` and
the log on the `CompileRun`. Requires TeX Live on the host/container
(`INSTALL_TEXLIVE=1` in deploy configs).

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `vault` — documents and output PDFs are `VaultFile`s; editor/play plugins
  wire the buttons.
- `workflows` — compiles can run as workflow nodes (`CompileRun.workflow_run`).
- `toto.texplay` — the Play-side compile/preview app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.transcription
========================================================================

# toto.transcription

Oya-style Django app for uploading audio/video files, running local transcription jobs, storing timestamped transcript segments, and exporting TXT / SRT / VTT / JSON.

This version supports both local Whisper engines:

- `openai-whisper` — original OpenAI Whisper package, requires FFmpeg on PATH.
- `faster-whisper` — CTranslate2-based Whisper runtime, good for CPU INT8 and GPU acceleration.

## Install

Copy the folder into your monorepo as:

```text
toto/transcription/
```

Add it in Studio mode:

```python
if BUILD_STUDIO:
    INSTALLED_APPS += [
        "toto.transcription",
    ]
```

Add URLs:

```python
path("transcription/", include("toto.transcription.urls")),
```

Then run:

```bash
python manage.py makemigrations transcription
python manage.py migrate
```

## Python packages

The app assumes you install the local engines you want to use:

```bash
pip install -U openai-whisper faster-whisper
```

For `openai-whisper`, also install FFmpeg and make sure `ffmpeg` is on PATH.

Examples:

```bash
# macOS
brew install ffmpeg

# Windows with Chocolatey
choco install ffmpeg

# Windows with Scoop
scoop install ffmpeg

# Debian / Ubuntu
sudo apt-get install ffmpeg
```

`faster-whisper` uses PyAV and usually does not need a separate FFmpeg executable for normal decoding.

## Engine choices

Every `TranscriptionJob` has an `engine` field:

```text
Default backend
openai-whisper
faster-whisper
Command backend
Custom callable
```

The default engine is controlled with:

```python
TRANSCRIPTION_DEFAULT_ENGINE = "faster_whisper"  # or "openai_whisper"
```

## openai-whisper backend

Use this when you want the original Whisper package and CLI-like behavior.

```python
TRANSCRIPTION_DEFAULT_ENGINE = "openai_whisper"
TRANSCRIPTION_OPENAI_WHISPER_MODEL = "small"  # tiny, base, small, medium, large, turbo
TRANSCRIPTION_OPENAI_WHISPER_DEVICE = None     # None, "cpu", or "cuda"
TRANSCRIPTION_OPENAI_WHISPER_FP16 = None       # None lets whisper decide; False is safer on CPU
```

For CPU-only Windows/Linux, this is usually safer:

```python
TRANSCRIPTION_OPENAI_WHISPER_DEVICE = "cpu"
TRANSCRIPTION_OPENAI_WHISPER_FP16 = False
```

## faster-whisper backend

Use this for better speed, CPU INT8, or NVIDIA GPU acceleration.

```python
TRANSCRIPTION_DEFAULT_ENGINE = "faster_whisper"
TRANSCRIPTION_FASTER_WHISPER_MODEL = "small"       # tiny, base, small, medium, large-v3, turbo, etc.
TRANSCRIPTION_FASTER_WHISPER_DEVICE = "cpu"        # "cpu" or "cuda"
TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE = "int8" # cpu: int8; cuda: float16 is common
TRANSCRIPTION_FASTER_WHISPER_BEAM_SIZE = 5
```

For NVIDIA GPU:

```python
TRANSCRIPTION_FASTER_WHISPER_DEVICE = "cuda"
TRANSCRIPTION_FASTER_WHISPER_COMPUTE_TYPE = "float16"
```

## Translation

Local Whisper supports transcription and translation **to English**. In the job form:

- leave `translate_to` empty for normal transcription;
- set `translate_to = en` for speech-to-English translation.

The service raises a clear error for other target languages because Whisper local backends are not speech-to-any-language translation engines.

## Custom callable backend

```python
TRANSCRIPTION_BACKEND = "myapp.transcription_backends.transcribe"
```

Callable signature:

```python
def transcribe(file_path: str, *, job, language: str = "") -> dict:
    return {
        "segments": [
            {"start_ms": 0, "end_ms": 1200, "speaker": "Speaker 1", "text": "Hello"},
        ]
    }
```

## Command backend

```python
TRANSCRIPTION_COMMAND = ["python", "manage.py", "my_transcriber", "{file}", "--language", "{language}"]
```

The command should print JSON to stdout. Either a list of segments or an object with a `segments` array is accepted.

## Development backend

For UI testing without a speech model:

```python
TRANSCRIPTION_BACKEND = "toto.transcription.backends.sidecar_txt_backend"
```

Place a sidecar text file next to your uploaded media in storage, named like:

```text
recording.mp3.txt
```

## Oya style

Templates extend `oya/base.html`, use `darkMode` Alpine bindings, Tailwind utility classes, Font Awesome icons, reusable `_form.html`, card grids, stats strips, and Chart.js dashboard panels.

## Boundaries

- `vault` stores original audio/video and generated transcript artifacts.
- `transcription` owns transcription collections, sources, jobs, segments, speakers, artifacts, and analytics events.
- AI summarization or action-item extraction should be added later through `steven` or `workflows` rather than mixed into the core STT flow.


========================================================================
  APP: toto.travels
========================================================================

# toto.travels

Travel & visit log — route journeys and place-visit reviews, surfaced as **Travels**
and **Visits** sub-tabs of the Locations app.

## Gating

Opt-in, off by default. Enable with `BUILD_TRAVELS=1` (or `manage.py … --travels`).
Its only dependencies are `locations` and `people`, both always installed — no tier
required. When the flag is off the app is not installed, its URLs are not mounted, and
the Locations tab bar hides the Travels/Visits tabs (`{% if "toto.travels"|app_installed %}`).

## Models

**`Travel`** — a group journey along a route.
- `participants` — M2M → `people.Person` (related_name `travels`).
- `route` — FK → `locations.Route`, `SET_NULL`, nullable (related_name `travels`).
- `info` — free-text notes about the travel.
- `score` — 1–5, nullable (enforced by the `travel_score_between_1_and_5` check constraint).
- `reviewed_at`, `starts_at` (required), `ends_at` (required) — datetimes.
- Properties: `duration`, `duration_display` (e.g. "2d 3h").

**`Visit`** — one person visiting one address.
- `participant` — FK → `people.Person`, `CASCADE` (related_name `visits`).
- `location` — FK → `locations.Address`, `SET_NULL`, nullable (related_name `visits`).
- `visited_at`, `ends_at`, `reviewed_at` — nullable datetimes.
- `score` — 1–5, nullable (`visit_score_between_1_and_5` check constraint).
- `review` — optional review text.
- Properties: `length_of_stay`, `length_of_stay_display`.

## Integration with Locations

The app is a satellite of `locations`; its templates extend `locations/base.html` and it
registers into the Locations plugin registries via `apps.py::ready()`
(`toto.core.plugin_autodiscover`):

- `plugins/map_plugins.py` → `LocationMapPlugin`: Travels drawn from `route.geometry`,
  Visits from `location.geometry`, as overlay features on the Locations map (with notes in popups).
- `plugins/url_plugins.py` → `LocationUrlPlugin`: deep-link resolvers (`travel_review`,
  `visit_review`, `travel_create`, `visit_create`, `address_visit_review`).
- `plugins/context_plugins.py` → `LocationContextPlugin`: injects `map_recent_travels` /
  `map_recent_visits` into the map's "Travels & Visits" overlay panel.
- `plugins/sidebar_plugins.py` — no-op stub (travels surface via the map overlay, not the sidebar).

### Address / Route notes

`locations.Address.note` and `locations.Route.notes` are free-text fields edited on the
Locations detail pages. They are shown read-only on the visit-review (location note) and
travel-review (route notes) pages and in Address/Route/Visit/Travel map popups.

## URLs (`app_name = "travels"`, mounted at `/travels/`)

- `""` → `my_travels` (Travels tab: travel list; links to Metrics and Add travel)
- `visits/` → `my_visits` (Visits tab: visit list; Add visit)
- `metrics/` → `travel_metrics`
- `travels/<pk>/review/`, `travels/new/`, `travels/<pk>/info/`, `travels/<pk>/delete/`
- `visits/new/`, `visits/<address_id>/review/`, `visits/<address_id>/review/submit/`, `visits/<pk>/delete/`

## Demo data

`manage.py ingress_travels --full` seeds ~18 travels and ~21 visits across several named
routes. It looks up existing `people.Person`, `locations.Route`, and `locations.Address`
rows, so seed `socialhub` and `locations` first (the `INGRESS_ALLOWED_APPS` ordering does
this automatically under `ingress_all`).


========================================================================
  APP: toto.ui
========================================================================

# toto.ui

Shared UI utilities and template helpers. No models, no URLs, no views — only template tags, context processors, and front-end building blocks shared across the system.

## What it contains

- `page.py` — template tag library and/or context processor providing shared variables (platform config, theme, navigation data) available in every template.

## Usage

Templates load shared context via `{% load ui_tags %}` or via the context processor configured in `TEMPLATES[0]['OPTIONS']['context_processors']`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.vault
========================================================================

# toto.vault

Encrypted file storage: buckets, directories, files, gateways, and storage backends.

## Concepts

- **Bucket** — named storage namespace with an optional quota (MB). Backed by local disk, S3-compatible, or remote Toto server.
- **VaultDirectory** — hierarchical folders within a bucket. Supports per-user access restrictions.
- **VaultFile** — uploaded file with type detection (image, PDF, text, SVG, video, audio…), content hash, and optional public access.
- **FileGateway** — upload endpoint linked to a directory. Controls allowed users, max file size, and whether uploads are made public.
- **StorageProvider** — named S3-compatible preset (AWS, OVH, MinIO…). Seeded by `ingress_storage_providers`.

## Storage backends

| Backend | `storage_backend` value | Notes |
|---|---|---|
| Local disk | `local` | Default. Files under `MEDIA_ROOT`. |
| S3-compatible | `s3` | Requires `storage_config` with `bucket_name`, `region_name`, and env-var credentials. |
| Remote Toto | `remote_toto` | Proxies to another Toto instance via its vault API. |

## Key coupling

- `library.LibraryItem.vault_file` — books, articles, audio, video reference their source file here.
- `ocr.OcrImage.vault_file` — OCR images are vault files.
- `texlab.LatexFile.vault_file` — LaTeX source files live in vault.
- `gervazy.EncryptedFile` is a separate encrypted-at-rest store; `vault` is for unencrypted / user-accessible files.
- `invoice.Invoice.bucket` — invoices can be linked to a bucket.

## Billing — tariffs optional

Vault can charge for storage via `toto.metering.charge`. The metering/tariffs apps are not part of the standard build (they live in `toto/limbo/`); when they are **not** installed all uploads proceed normally, uncharged.

When tariffs is installed, upload views use:

```python
from toto.metering.charge import get_tariff_for_user, check_user_can_act, charge_user

tariff = get_tariff_for_user(user, "vault")
check_user_can_act(user, tariff, "storage.request", 1)       # pre-flight balance check
charge_user(user, tariff, "storage.request", 1, ...)         # drain balance after upload
```

Charge failure is non-fatal — metering still records the event.

## Metering events

| Metric | Unit | When |
|---|---|---|
| `storage.request` | request | Every file upload |
| `storage.transfer_mb` | MB | Every file upload (actual bytes transferred) |

## Ingress

`python manage.py ingress_vault` seeds demo buckets, directories, and files. Tariff and invoice seeding is skipped when `toto.tariffs` / `toto.invoice` are not installed.

## Enigma JSON API

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/vault/api/files/` | List own files (auth required) |
| POST | `/vault/api/files/upload/` | Upload file — multipart `file` + `title` (auth required) |
| GET | `/vault/api/files/{key}/` | File detail (auth required) |
| DELETE | `/vault/api/files/{key}/` | Delete own file → 204; others → 403 (auth required) |
| GET | `/vault/api/files/{key}/download/` | Redirect to file download (auth required) |

Upload auto-creates a personal bucket `personal-{username}` if one doesn't exist.

### Testing
```bash
cd portal && python manage.py test toto.vault.tests_api
```


========================================================================
  APP: toto.verbena
========================================================================

# toto.verbena

Abstract page/section/tag system. Provides three base models inherited by every content app in the system. Verbena itself has no concrete models and no URL routes.

## Models (abstract bases)

- `AbstractTag` — extends `DomainEntity`. A tagging primitive. Concrete subclasses: `palimpsest.Tag`, `socialhub.CommunityNewsTopic`, `memo.Tag`, `library.*Tag`.

- `AbstractPage` — extends `DomainEntity`. A titled, slugged, rich-text document. Fields: `title`, `slug`, `description` (intro/summary), `cover_image`, `is_published`, `published_at`, `created_at`, `updated_at`. Concrete subclasses: `palimpsest.Page`, `kanban.DocumentationPage`, `academy.Script`.

- `AbstractSection` — extends `DomainEntity`. A content block within a page. Fields: `page` (generic FK via the concrete subclass), `title`, `order`, `content` (HTML/markdown body), `author` (FK to `people.Person`, nullable), `created_at`. Concrete subclasses: `palimpsest.Section`, `kanban.DocumentationSection`, `academy.ScriptSection`, `socialhub.CommunityNewsPost`.

## Key coupling

Every content app inherits from these bases. Changes to verbena abstract fields propagate to all concrete subclasses via Django's multi-table inheritance.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — AbstractSection.author FK to Person────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.vod
========================================================================

# toto.vod

Reusable Django VOD app for industrial robotics footage.

This version is intentionally **connected** to your existing Toto apps:

- **Storage** is reused from `vault`:
  - source videos are `vault.VaultFile`
  - collections point to `vault.Bucket`
  - generated HLS output uses the same Django storage backend and stores paths on `VodVideo`
- **Subscriptions** are reused from `toto.subscriptions`:
  - subscriber-gated videos point to `subscriptions.SubscriptionPlan`
  - access checks use active/trialing `subscriptions.Subscription`
  - watch-time metering calls `toto.subscriptions.services.record_usage`
  - local playback events can link to `subscriptions.SubscriptionUsage`
- **Invoices** are reused from the existing `invoices` app:
  - invoice-gated videos create an `invoices.Invoice`
  - `VodAccessGrant` unlocks only after the linked invoice is paid

The app does **not** reimplement subscription plans, customers, invoices, payments, ledgers, assets, or vault storage.

## Install

```python
INSTALLED_APPS = [
    # existing dependencies first
    "toto.vault",          # app_label: vault
    "toto.subscriptions",  # app_label: subscriptions
    "toto.invoices",       # app_label: invoices
    "toto.vod",
]
```

Project URLs:

```python
path("vod/", include("toto.vod.urls")),
```

Then:

```bash
python manage.py migrate
```

## Optional settings

```python
VOD_FFMPEG_BIN = "ffmpeg"
VOD_FFMPEG_VIDEO_CODEC = "libx264"
VOD_FFMPEG_AUDIO_CODEC = "aac"
VOD_FFMPEG_AUDIO_BITRATE = "128k"
VOD_FFMPEG_PRESET = "veryfast"

# Optional when your User -> people.Person relation is custom.
VOD_PERSON_RESOLVER = "path.to.resolve_person"  # callable(user) -> people.Person

# Optional custom active subscription resolver.
VOD_SUBSCRIPTION_RESOLVER = "path.to.resolve_subscription"  # callable(user, plan) -> Subscription | None
```

## Access modes

`VodCollection.access_mode` controls who can watch:

- `public` — anyone, including unauthenticated users
- `private` — only users in the `readers` or `writers` M2M lists (or the owner)

Access is collection-level; individual `VodVideo` records inherit from their collection.
`VodAccessGrant` records can grant one-off access tied to a `subscriptions.Subscription` or `invoice.Invoice`.

## Upload and HLS

Upload in the UI:

```text
/vod/upload/
```

Import a server-side robotics video:

```bash
python manage.py ingress_robotics_vod /path/to/robot-cell.mp4 --publish --build-hls
```

Build HLS output:

```bash
python manage.py build_vod_hls 123 --force
```

This produces storage paths like:

```text
vod/hls/industrial-robotics/robot-cell/index.m3u8
vod/hls/industrial-robotics/robot-cell/segment_00000.ts
vod/hls/industrial-robotics/robot-cell/segment_00001.ts
```

The browser page uses HLS.js when native HLS support is unavailable.

## Notes

- VOD source files must be unencrypted `vault.VaultFile` records with `file_type="video"`.
- HLS/TS files are public/media-storage assets. Put S3/CloudFront in front of the storage backend for production.
- The app expects app labels `vault`, `subscriptions`, and `invoices`.


========================================================================
  APP: toto.weather
========================================================================

# toto.weather

*(Studio only — requires BUILD_STUDIO=1)*

Weather data ingestion and forecasting. Stores observations and forecast sessions, typically populated by workflow nodes that call external weather APIs.

## Models

- `WeatherSettings` — platform-wide weather configuration. Fields: `provider` (slug of weather API), `api_endpoint`, `units` (`metric / imperial`), `update_interval_minutes`, `is_active`, `community` (FK, nullable).

- `WeatherObservation` — a single observed data point. Fields: `address` (FK to `locations.Address`), `observed_at`, `temperature_c`, `humidity_pct`, `wind_speed_kmh`, `wind_direction_deg`, `precipitation_mm`, `condition` (slug), `raw_data` (JSON), `workflow_run` (FK to `workflows.WorkflowRun`, nullable).

- `ForecastSession` — a batch of forecast points generated in one API call. Fields: `address` (FK), `generated_at`, `provider`, `workflow_run` FK, `raw_response` (JSON).

- `ForecastPoint` — one time-step within a forecast. Fields: `session` FK, `forecast_at`, `address` FK, `temperature_c`, `humidity_pct`, `wind_speed_kmh`, `precipitation_mm`, `condition`.

## Key coupling

- `locations.Address` — observations and forecasts are anchored to addresses.
- `workflows.WorkflowRun` — weather fetch is a workflow node type; each run links its observation/forecast to the triggering workflow run.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `locations` — WeatherObservation.location FK to Address
- `workflows` — ForecastSession triggered by workflow node; WorkflowRun FK────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.workflows
========================================================================

# toto.workflows

*(Studio only — requires BUILD_STUDIO=1)*

DAG-based workflow orchestration engine. Workflows define directed graphs of nodes; runs execute nodes in topological order, with edges carrying data between them. Used by `texlab`, `weather`, and `steven`.

## Models

- `LambdaFunction` — a Python snippet that can be called as a workflow node. Fields: `name`, `slug`, `code` (Python source), `signature` (JSON schema for inputs/outputs), `is_active`.

- `Workflow` — a named DAG. Fields: `name`, `slug`, `community` (FK, nullable), `is_active`, `metadata`.

- `ReportTemplate` — a Jinja/HTML template for workflow report output. Fields: `name`, `slug`, `template_source`, `is_active`.

- `WorkflowNode` — a node in the workflow DAG. Fields: `workflow` FK, `name`, `node_type` (`lambda / http_request / condition / merge / split / kernel_cell / compile_latex / weather_fetch / agent_call`), `config` (JSON — node-specific parameters), `position_x` / `position_y`.

- `WorkflowEdge` — a directed connection between two nodes. Fields: `workflow`, `source` / `target` (FKs to `WorkflowNode`), `condition` (JSON — optional expression that must evaluate truthy), `data_mapping` (JSON — how to map source outputs to target inputs).

- `WorkflowRun` — one execution of a workflow. Fields: `workflow`, `status` (`pending / running / success / failed / cancelled`), `triggered_by` (FK to `people.Person`, nullable), `started_at`, `finished_at`, `input_data` / `output_data` (JSON), `error_message`.

- `WorkflowNodeRun` — execution of one node within a run. Fields: `run` FK, `node` FK, `status`, `input_data` / `output_data` (JSON), `started_at`, `finished_at`, `error_message`, `retries`.

- `WorkflowEdgeRun` — data flowing across one edge in a run. Fields: `run`, `edge`, `data` (JSON), `transferred_at`.

- `Report` — a rendered output document produced by a workflow run. Fields: `run` FK, `template` FK, `title`, `rendered_html`, `vault_file` (FK to `vault.VaultFile`, nullable — PDF export), `created_at`.

- `ReportPage` — a page within a multi-page report. Fields: `report` FK, `page_number`, `content` (HTML).

## Key coupling

- `texlab.CompileRun.workflow_run` — LaTeX compiles can be workflow nodes.
- `weather.WeatherObservation.workflow_run` / `ForecastSession.workflow_run` — weather fetches are workflow nodes.
- `mandragora.Cell` — kernel cell execution is a workflow node type.
- `steven.AgentRun` — agent calls are a workflow node type.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `mandragora` — ComputeKernel FK — workflow nodes can execute in a mandragora kernel────────────────────────────────────────────────────────────────────────

