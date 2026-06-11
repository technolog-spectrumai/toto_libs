_# toto

**toto** is a Django-based community platform. Communities on toto can organise members, run projects, manage shared identity, store files securely, create media, and operate a knowledge graph — all tied together through a shared encryption layer and cryptographic identity foundation.

---

## The Idea

A community on toto has members, a shared identity system, a project management layer, a file vault, and optionally a real-time studio layer for chat, notebooks, media processing, and AI agents. The graph of all relationships lives in Neo4j, synchronized from Postgres through a single boundary app. Secrets and signing keys are encrypted at rest per user.

The economic, governance, and operational layers (assets, assembly, magistrate, tribunal, bazaar, logistics, etc.) are archived in `toto/limbo/` — documented in `limbo/economy.md` — and can be reintroduced as a coherent block when the architecture is ready.

---

## System Traits

- **Django monorepo.** Two active Django projects: `toto/` (platform library), `portal/` (management portal). `regis/` (geophysical/economic simulation) is a separate project.
- **Hierarchical encryption.** `gervazy` implements a three-tier AES-256-GCM key hierarchy: password → Argon2id KDF → UKEK → VMK → DEK → encrypted objects (secrets, files, private keys).
- **OIDC provider.** `sso_master` is a full OpenID Connect server. Signing keys are RSA stored encrypted in gervazy.
- **Graph layer.** `ravioli` is the sole Neo4j boundary. Apps emit `GraphChangeEvent` records; a Celery worker drains them into Neo4j. No app ever calls Neo4j directly.
- **Real-time over WebSockets.** Django Channels powers chat (`telegraph`), whiteboard (`sketch`), LaTeX compilation (`texlab`), and compute kernels (`mandragora`).
- **Usage quota.** `quota` provides lightweight usage tracking and rate limiting. Apps call `check_quota()` before actions and `record_usage()` after. Each app owns its own metrics view.
- **Celery + Redis.** Background tasks handle graph sync, workflows, and media processing.
- **Modular deployment.** `deploy.py` reads YAML configs to build settings for each deployment target.

---

## Deployment: Base vs Studio

The single most important architectural divide is `BUILD_STUDIO`.

| | Base (`portal_mini`) | Studio (`portal_max`) |
|---|---|---|
| **Server** | gunicorn (WSGI) | uvicorn/daphne (ASGI) |
| **WebSockets** | ✗ | ✓ (Django Channels + Redis) |
| **Neo4j** | ✗ | ✓ (via ravioli) |
| **Compute kernels** | ✗ | ✓ (mandragora + ZMQ) |
| **Infrastructure** | Postgres + Redis + Nginx | + Neo4j + ZMQ kernel server |

Build flags: `BUILD_STUDIO`, `BUILD_NEO4J`, `BUILD_LABS`

Studio apps (only when `BUILD_STUDIO=1`): `telegraph`, `mandragora`, `workflows`, `videomant`
Neo4j apps (only when `BUILD_NEO4J=1`): `ravioli`, `bento`, `vicuna`
Labs apps (only when `BUILD_LABS=1`): `texlab`, `sketch`, `ocr`, `steven`

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
| `conf` | Platform configuration helpers. |

### Security
| App | Purpose |
|---|---|
| `gervazy` | Encryption-at-rest. Three-tier AES-256-GCM key hierarchy per user. |
| `vault` | User file storage. Buckets, soft-delete VaultFiles, FileGateway sharing, quota-tracked uploads. |

### Identity
| App | Purpose |
|---|---|
| `people` | Primary identity object. `Person` is the human actor across all domains. |
| `sso_core` | Shared OIDC manifest schemas — pure Python dataclasses, no models. |
| `sso_master` | Full OIDC 1.0 provider. Issues tokens, manages signing keys. |
| `sso_client` | OIDC consumer config. Stores the provider endpoint imported from sso_master. |

### Community
| App | Purpose |
|---|---|
| `socialhub` | Community model. Membership, news, hierarchy, administrata view. |
| `events` | Scheduled event calendar. EventInvite and Availability blocks. |
| `polls` | Informal community polling. |

### Project Management
| App | Purpose |
|---|---|
| `kanban` | Project and task management. Projects → Campaigns → Missions → Tasks, practitioner roles, sprints. |

### Content
| App | Purpose |
|---|---|
| `memo` | Flashcard decks and SVG diagrams. Deck-based lecture material. |
| `verbena` | Abstract page/section/tag bases inherited by content apps. |

### Geography
| App | Purpose |
|---|---|
| `locations` | PostGIS geographic layer. Address, Territory, Zone, Route, MapLayer. |

### Metering
| App | Purpose |
|---|---|
| `quota` | Usage tracking and quota enforcement. `QuotaPolicy` (limit/period/mode) + `UsageEvent` (idempotent records). Public API: `check_quota()`, `record_usage()`, `usage_summary()`. |

### Studio (requires `BUILD_STUDIO=1`)
| App | Purpose |
|---|---|
| `telegraph` | Real-time group chat over WebSockets. |
| `mandragora` | Jupyter-style compute kernels over WebSockets. Notebooks, cells, ZMQ kernel backend. |
| `workflows` | DAG-based automation engine. Lambda/split/join/report nodes. |
| `videomant` | Safe ffmpeg/ffprobe media processing via Celery + workflows. |
| `vod` | Video on demand. Collections, HLS streaming, access control. Quota-tracked playback. |
| `transcription` | Audio/video transcription. Collections, timestamped segments, speaker detection. Quota-tracked jobs. |

### Neo4j (requires `BUILD_NEO4J=1`)
| App | Purpose |
|---|---|
| `ravioli` | Sole Neo4j boundary. GraphChangeEvent drain → graph upserts. Saved Cypher queries. Owns the neomodel connection. Quota-tracked. |
| `bento` | First-class Neo4j graph editor. SQL holds only node-category/edge-type templates; nodes and relationships live in Neo4j. Requires `ravioli`. No quota. |
| `vicuna` | Ollama / Qwen service layer (no UI). |

### Labs (requires `BUILD_LABS=1`)
| App | Purpose |
|---|---|
| `texlab` | Async LaTeX compilation with live log streaming. |
| `sketch` | Collaborative real-time whiteboard. |
| `ocr` | Document OCR pipeline. Image → transform chain → text lines. |
| `steven` | AI agent management. AgentConnector, profiles, tools, conversation history. Quota-tracked inference. |

---

## Key Relations

```
Federation
  └── many Community (socialhub)
        ├── many Person (members)
        │     ├── User (auth)
        │     └── UserStrongbox (gervazy) → VaultMasterKey → DEK → EncryptedSecrets
        ├── News, membership applications
        └── Events, Polls

Person
  ├── Practitioner (kanban) → Task → Mission → Campaign → Project
  ├── Participant (telegraph) → Room (WebSocket)
  └── VaultFile (vault) → Bucket

ravioli (Neo4j)
  ← GraphChangeEvent (emitted by signals across all apps)
  → Neo4j nodes + relationships

quota
  ← check_quota() called before metered actions in vault, steven, transcription, ravioli, vod
  ← record_usage() called after metered actions
  → UsageEvent records per app/metric/subject
  → usage_summary() feeds per-app metrics views
```

---

## Limbo

The following app clusters are archived in `toto/limbo/` and disconnected from the running system. They can be reintroduced as coherent blocks:

| Cluster | Apps | Notes |
|---|---|---|
| **Financial ledger** | assets, claims, contracts, instruments, tariffs, taxes, invoice | Full double-entry ledger + Lapis smart contracts. See `limbo/economy.md`. |
| **Peer economy** | bourse, subscriptions, payroll, loans, insurance, leasing, mission_economy | Requires financial ledger layer. |
| **Commerce** | bazaar, logistics | Marketplace + package tracking. |
| **Governance** | assembly, magistrate, tribunal, senate, capitol, treasury | Democratic governance, hash-chained decisions, DAO-capable. |
| **Emergency ops** | mobilization, response, incidents, detections, robots, tactical, inventory | Emergency command pipeline + field operations. |
| **Knowledge** | academy, library, palimpsest | LMS, reference library, collaborative writing. |
| **Learning tools** | quizzes, competence | Quiz engine, skills registry. |
| **Geo-ops** | weather, travels | Weather ingestion, route journeys. |

Import path for any limbo app: `toto.limbo.<app>` is NOT on `sys.path`. To reintroduce, move the directory back to `toto/toto/`, add to `INSTALLED_APPS`, and audit migration dependencies against current active app schemas.

---

## Simulation (separate project)

| App | Purpose |
|---|---|
| `regis.geophysics` | Planet generation engine for synthetic geography. |
| `regis.economy` | Macroeconomic simulation with cohorts, labor, fiscal, market, and infrastructure subsystems. |_
