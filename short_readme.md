# toto

**toto** is a Django-based platform for running tokenized community economies. Communities on toto can issue assets, vote on rules, levy taxes, trade, run projects, learn, and settle disputes — all tied together through a shared identity and cryptographic foundation.

---

## The Idea

A community on toto operates like a mini-state: it has members, money, rules, a parliament, a court, and an economy. Everything economically meaningful can be tokenized and tracked on a double-entry ledger. Governance decisions are immutable and hash-chained. Secrets are encrypted at rest with a per-user vault. The graph of all relationships lives in Neo4j, synchronized from Postgres through a single boundary app.

---

## System Traits

- **Django monorepo.** Three Django projects: `toto/` (platform), `portal/` (management portal), `regis/` (geophysical/economic simulation).
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

| | Base (`portal_mini`) | Studio (`portal_max`) |
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
| `regis.geophysics` | Planet generation engine for synthetic geography. |
| `regis.economy` | Macroeconomic simulation with cohorts, labor, fiscal, market, and infrastructure subsystems. |

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
