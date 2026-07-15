# toto

**toto** is a modular Django *app library* — a "community operating system"
packaged as a single installable Python distribution (`pip install -e .`).
It bundles ~45 apps spanning identity and single-sign-on, encrypted storage, a
Neo4j knowledge-graph layer, real-time collaboration, media processing, and
pluggable AI — and lets a host project assemble exactly the subset it needs
through per-feature build flags.

This repository is the **standalone library** extracted from the original
monorepo (history preserved back to the "wrap in another dir" refactoring;
earlier flat-layout history under the old app names was not carried over).

toto is not run directly. It is mounted by a **host project** that provides the
settings, URLs, and server entrypoint:

- **portal** — the full management portal / web app; the reference deployment.
  Lives in the separate `portal` repository, which also contains **faros**.
- **faros** — a minimal Tor-only server profile (no Neo4j, no heavy compute),
  maintained as a build profile inside the `portal` repository.
- **edge** — desktop + Android artifacts (Tauri clients: enigma / aurora),
  maintained separately.

> **Note (2026):** toto was previously a "tokenized community economy" platform —
> a double-entry ledger, financial instruments, an assembly/tribunal governance
> stack, a bazaar marketplace, and a `regis` simulation project. Those apps have
> been **removed**. Today's toto is focused on identity, community, the knowledge
> graph, real-time collaboration, and AI. This README reflects the apps that
> actually exist in `toto/`.

---

## Architecture principles

- **One package, many hosts.** Every app lives under the `toto.` namespace and is
  installed from this one distribution. Host projects (`portal`, `faros`, `edge`)
  differ only in settings, which apps they enable, and their server entrypoint.
- **Per-feature composition.** A deployment is defined by `BUILD_*` flags, not by
  code branches. The same tree can ship as *WSGI + Postgres + Redis* or *ASGI +
  Neo4j + Celery + kernel server + AI*. See the portal repository's README
  ("what gets into what build") for the exact matrix.
- **`Person` is the identity anchor.** Nearly every app links to `people.Person`
  (one-to-one with `auth.User`, nullable) rather than to `User` directly.
- **Encryption at rest is centralized.** `gervazy` owns a three-tier AES-256-GCM
  key hierarchy; every other app that needs a secret, key, or encrypted file
  stores it there — never in plaintext, never in a config blob.
- **`ravioli` is the sole Neo4j boundary.** No app imports the Bolt driver or
  neomodel directly. The graph *shape* is declared as YAML in `sql_neo4j_sync`;
  writes go through ravioli's per-object "Export to graph" (checksum-diffed,
  history-snapshotted) or the opt-in bulk projection.
- **`sso_master` is a full OIDC provider.** Any toto app or external service
  (e.g. Grafana) can authenticate against the portal. Signing keys are RSA,
  stored encrypted in gervazy.
- **Real-time is Django Channels.** Chat, whiteboard, code/LaTeX editors, and
  compute kernels are WebSocket consumers; enabling any of them flips the host to ASGI.

---

## App catalog

Legend — **base** = always installed by the portal host; the rest are gated by a
`BUILD_*` flag (shown). Infra tags: 🔷 Neo4j · 🔌 WebSockets/Channels · ⚙️ Celery ·
🧩 native binary. See the portal repository's build matrix
("what gets into what build") for the exact flag → app → infra mapping.

### Foundation & shared bases *(base)*
| App | Purpose |
|---|---|
| `core` | Platform singleton + tenant identity: `Platform`, `Federation`, branding (`Theme`/`ColorMix`/`Font`), the `DomainEntity` abstract base, the plugin registry, and `PlatformMiddleware`. |
| `api` | Outbound API connector registry (`ApiConnector`/`Connector`) and SMTP `EmailService` config. Secrets are referenced from gervazy, never stored inline. |
| `verbena` | Abstract content bases only (`AbstractPage`/`AbstractSection`/`AbstractTag`) inherited by content apps; no tables of its own. |
| `ui` | Shared template tags, context processors, base page layout. No models. |
| `ingress` | `IngressCommand` base class behind every `manage.py ingress_*` seed command. |
| `quota` | Usage-quota policy engine (`QuotaPolicy` + `UsageEvent`) for rate / consumption limits. |
| `backup` | Signs outbound backup archives with a gervazy RSA key; stores and verifies received archives. |

### Security & storage *(base)*
| App | Purpose |
|---|---|
| `gervazy` | Encryption-at-rest vault. Three-tier AES-256-GCM key hierarchy (password → Argon2id → UKEK → VMK → DEK → objects), Ed25519 signing, append-only crypto audit log. Holds every other app's secrets/keys. |
| `vault` | User file storage: buckets, directories, soft-deleted `VaultFile`s, `FileGateway` sharing, pluggable storage providers. |

### Identity & SSO
| App | Purpose | Gate |
|---|---|---|
| `people` | `Person` — the shared identity anchor referenced across all domains. | base |
| `sso_core` | Shared OIDC manifest / connection-bundle dataclasses + JSON schemas. No models. | base |
| `sso_master` | Full OpenID Connect 1.0 **provider**: authorize/token/userinfo/JWKS, signing key encrypted in gervazy, PKCE, relying-party registration. | base |
| `sso_client` | OIDC **consumer** config (`OIDCProviderConfig`) imported from an sso_master bundle. Used by non-portal hosts (e.g. edge). | host-specific |

### Community, work & content *(base)*
| App | Purpose |
|---|---|
| `socialhub` | Community model: `Community` as the primary grouping unit, referral-gated `MembershipApplication`, hierarchy, news, administrata view. |
| `events` | Scheduled events + personal availability calendar. `EventBase` is the abstract parent of `detections`-style time-anchored records. |
| `polls` | Lightweight ad-hoc community polling (Poll / Option / Vote). Non-binding. |
| `kanban` | Project management: Project → Campaign → Mission → Sprint → Task, with practitioner allowances paid on a Celery-beat schedule. ⚙️ |
| `notarius` | Contract signing: renders admin-editable LaTeX `ContractTemplate`s to signed PDFs via async `ContractPdfJob`. ⚙️ 🧩 texlive |
| `memo` | File-based `.pml` presentation viewer + browser editor. Presentations live as self-contained vault files; DB models were dropped. |
| `vod` | Vault play-plugin host for video playback. Model-less (VOD tables dropped). |
| `locations` | PostGIS (SRID 4326) geographic substrate: Address, Territory, Zone, Route, MapLayer. |
| `transcription` | Audio/video transcription: local Whisper (openai-whisper / faster-whisper) jobs, timestamped segments, TXT/SRT/VTT/JSON export. ⚙️ 🧩 ffmpeg |

### Real-time & collaboration
| App | Purpose | Gate |
|---|---|---|
| `telegraph` | Relay/forum ("Discord-style") chat backend: persistent per-room messages over Channels + a Bearer-token JSON API. MLS end-to-end encryption via the rotor WASM. | `BUILD_CHAT` · 🔌 |
| `sketch` | Collaborative real-time whiteboard (Board / BoardObject) broadcast over Channels. | `BUILD_SKETCH` · 🔌 |
| `editor` | Shared collaborative text/JSON editor (ACE + diff-match-patch sync consumer) for vault files. No models. | `BUILD_LATEX`/`BUILD_PYEDITOR` · 🔌 |
| `texlab` | LaTeX compilation service: workspaces of vault files; `CompileRun`s stream logs and store output PDFs. | `BUILD_LATEX` · 🔌 ⚙️ 🧩 texlive |
| `texplay` | Compiles a single `.tex` vault file to PDF via an async `TexPlayJob` (workflow node entrypoint). | `BUILD_LATEX` · ⚙️ 🧩 texlive |
| `antaresia` | Runs Python scripts stored in vault (sandboxed subprocess) with a WebSocket file-sync editor and async `PythonRun`s. | `BUILD_PYEDITOR` · 🔌 ⚙️ |
| `mandragora` | Jupyter-style compute kernels over WebSockets; cells execute against a ZMQ kernel-server process. Runs workflow lambda nodes. | `BUILD_WORKFLOWS` · 🔌 ⚙️ |

### Automation
| App | Purpose | Gate |
|---|---|---|
| `workflows` | DAG workflow engine (Workflow / Node / Edge / LambdaFunction / ReportTemplate) executing nodes in topological order. Drives texlab, weather, steven. | `BUILD_WORKFLOWS` · ⚙️ |
| `weather` | Weather observation/forecast storage, populated by workflow nodes calling external APIs. | `BUILD_WEATHER` · ⚙️ |

### Media services
| App | Purpose | Gate |
|---|---|---|
| `fileservices` | Pluggable file-processing runner (`FileServiceRun`) dispatching plugin/workflow tasks over vault files (incl. ffmpeg-based ones). | `BUILD_FILESERVICES` · ⚙️ 🧩 ffmpeg |
| `manta` | Legacy one-stop command builder wrapping ffmpeg/ffprobe/transcribe with minimal `FileJob`/`MediaJob` persistence. | `BUILD_MANTA` · ⚙️ 🧩 ffmpeg |

### Knowledge graph *(Neo4j)* — `BUILD_GRAPH` / `BUILD_NEO4J`
| App | Purpose |
|---|---|
| `ravioli` | The sole Neo4j boundary (Bolt / neomodel): saved `CypherQuery`s, search, NetworkX analysis, NeoJSON import/export, and per-object "Export to graph" sync. 🔷 ⚙️ |
| `sql_neo4j_sync` | SQL→Neo4j projection layer: owns the graph shape as YAML and mirrors Django rows into nodes/relationships (all I/O via ravioli). 🔷 ⚙️ |
| `bento` | First-class Neo4j graph editor. Node-category / edge-type *templates* live in SQL (`BentoCategory`/`BentoEdgeType`); actual nodes/edges live in Neo4j via runtime-built neomodel classes. 🔷 |
| `ingestor` | Text → Bento-validated graph patch: deterministic spaCy NER + rapidfuzz produce reviewable `IngestProposal`s applied through bento. 🔷 |
| `neo_editor` | Dual-mode `.neojson` vault editor (Ace JSON edit + Cytoscape read-only preview), reused by vault and ravioli. No models. 🔷 |
| `ocr` | Stateless OCR sub-tab of the Knowledge Graph: pytesseract extracts text from a screenshot, optionally piped to the ingestor. No models. `BUILD_OCR` · 🔷 🧩 tesseract |
| `connectors` | External APIs → Bento-validated graph patches: scheduled/manual `DataConnector` runs emit ingestor proposals. `BUILD_CONNECTORS` · 🔷 ⚙️ |
| `formica` | Ant/termite-colony agent that curates the graph each cycle (pheromone trails, repair, prune, propose links) via reviewed `FormicaProposal`s. `BUILD_FORMICA` · 🔷 ⚙️ |

### AI
| App | Purpose | Gate |
|---|---|---|
| `sabbia` | Headless agentic chatbot backend: hosts chat `Agent`s over WebSockets with pluggable endpoints (OpenAI, or Ollama via vicuna) and gervazy-encrypted creds. GraphRAG over the ravioli graph. No UI. | `BUILD_SABBIA` · 🔌 |
| `steven` | Thin site-wide floating "Ask AI" widget that opens a WebSocket to sabbia. No models. | `BUILD_STEVEN` (implies sabbia) · 🔌 |
| `vicuna` | Ollama deployment registry (`OllamaServer`/`OllamaModel`) proxying local Ollama chat/embeddings. | `BUILD_VICUNA` (graph or sabbia-ollama) |

### Tor / P2P *(faros-only — not installed on the portal)*
| App | Purpose |
|---|---|
| `aster` | Tor signalling directory: binds iroh `NodeId`s to owners and stores current relay URLs with TTL. Never stores IPs. |
| `nomad` | App-managed Tor onion identity: metadata/history of `.onion` addresses (secret key lives in a volume). |

---

## Cross-cutting flows

```
Person (people)
  ├── auth.User (nullable)                 identity anchor for every app
  ├── UserStrongbox (gervazy)              → VMK → DEK → EncryptedSecret / EncryptedFile / EncryptedPrivateKey
  ├── Community membership (socialhub)     referral-gated MembershipApplication
  └── Practitioner (kanban) → Task → Mission → Campaign → Project

sso_master (OIDC provider)
  → SSOSigningKey → gervazy.EncryptedPrivateKey (private key never in plaintext)
  → authenticates: portal apps, Grafana, edge clients (via sso_client)

Knowledge graph (opt-in, BUILD_GRAPH)
  text ─┐
  API  ─┼→ ingestor (spaCy/rapidfuzz) → IngestProposal → review → bento → Neo4j
  OCR  ─┘                                                     ▲
  connectors (scheduled ETL) ────────────────────────────────┘
  formica (colony) ── curates ──→ Neo4j ──(sole boundary)── ravioli ──→ Cypher / analysis / NeoJSON
  Django rows ── sql_neo4j_sync (YAML shape) ──→ ravioli ──→ Neo4j

Real-time (Channels / ASGI)
  telegraph (chat, MLS) · sketch (whiteboard) · editor/texlab/antaresia (code+LaTeX)
  · mandragora (compute kernels, ZMQ) · sabbia/steven (AI)

Automation (BUILD_WORKFLOWS)
  workflows DAG → lambda nodes (mandragora kernel) → weather / texplay / fileservices / reports
```

---

## Host integration API

Small stable modules hosts use instead of hardcoding toto internals:

- **`toto.features`** — `resolve_features(get)` turns BUILD_*/INSTALL_* flags
  (from `os.environ.get` or a deploy config dict) into effective feature
  booleans, tiers, and native-binary needs. Single source for the dependency
  closure previously duplicated between host settings and deploy tooling.
- **`toto.registry`** — `BASE_APPS`, `FEATURE_APPS`, `FAROS_APPS`,
  `TASK_MODULES` (Celery autodiscovery), `has_app()` capability check.
- **`toto.routing`** — `collect_websocket_urlpatterns()` gathers Channels
  websocket routes from installed toto apps for the host ASGI router.
- **`toto.schedules`** — `beat_schedule(...)` builds the Celery beat entries
  for the enabled features.
- **`toto.conf`** — host-configurable filesystem locations; hosts should set
  **`TOTO_DATA_DIR`** (seed/branding data: fonts.json, themes/, img/) and
  **`TOTO_RUN_DIR`** (vault-password bundles) in settings. Legacy monorepo
  path resolution remains the fallback.
- **`toto.__version__`** — the package version (single-sourced into wheel
  metadata).

---

## Development

- **Package name:** `toto` (`pyproject.toml`), installed editable into the host.
- **Run tests from the host project dir** (e.g. `portal/`), *not* the repo root —
  running from the root causes a `toto.toto.X` double-import model conflict.
- **Aggregate docs:** `build_total_readme.py` concatenates every app's own
  `README.md` (prepended with `short_readme.md`) into `total_readme.md`.
- **Build / deploy:** owned by the host project. For portal, everything runs
  through `portal/scripts/deploy.py` — see the portal repository's README.

---

## Summary

toto is a composable Django app library for building community platforms: a shared
`Person` identity with a full OIDC provider, centralized encryption-at-rest, a
Neo4j knowledge-graph layer behind a single boundary, real-time collaboration over
WebSockets, media and document pipelines, and pluggable AI agents. Host projects
(`portal`, `faros`, `edge`) mount the subset they need through per-feature build
flags — the same source tree scaling from a lean WSGI site to a full ASGI stack
with a graph database, background workers, a compute kernel, and AI.
