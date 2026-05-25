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
- **Graph layer.** `ravioli` is the sole Neo4j boundary. Apps emit `GraphChangeEvent` records; a Celery worker drains them into Neo4j. No app ever calls Neo4j directly.
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
| `ravioli` | Sole Neo4j boundary. GraphChangeEvent drain → graph upserts. |
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



========================================================================
========================================================================



========================================================================
  APP: toto.academy
========================================================================

# toto.academy

Learning management system (LMS). Courses, modules, lessons, student enrollment, certificates, cohorts, and learning paths.

## Purpose

A `Teacher` creates a `Course` structured into `CourseModule` → `Lesson` chains. Each lesson is backed by a `MemoDeck` of flashcards. A module can have an exam `Quiz`; passing unlocks a `SkillBadge`. Students enroll, work through lessons, take quizzes, and earn certificates. `Cohort` records let a teacher run a group through a course on a shared schedule. `LearningPath` sequences badges into progressions for structured skill development.

## Models

- `Teacher` — a `Person` authorized to create courses. Fields: `person` (FK), `community` (FK), `is_active`, `bio`.

- `Course` — a structured learning program. Fields: `teacher` FK, `community` FK, `title`, `description`, `cover_image`, `status` (`draft / published / archived`), `is_public`, `price_base_units`, `price_asset`, `max_students`, `tags` (M2M).

- `CourseModule` — a chapter within a course. Fields: `course`, `title`, `description`, `order`, `is_published`.

- `Lesson` — a single learning unit within a module. Fields: `module`, `title`, `content` (rich text), `lesson_type` (`text / video / audio / quiz / deck`), `memo_deck` (FK to `memo.MemoDeck`, nullable — for flashcard lessons), `order`, `duration_minutes`.

- `Student` — a `Person` enrolled in the academy. Fields: `person` (OneToOne), `community` FK, `is_active`.

- `StudentBadge` — an achievement badge awarded to a student. Fields: `student`, `badge_type` (slug), `awarded_at`, `metadata`.

- `CourseEnrollment` — enrollment record. Fields: `student`, `course`, `status` (`enrolled / completed / dropped`), `enrolled_at`, `completed_at`, `progress_percent`.

- `Certificate` — issued on course completion. Fields: `enrollment` (OneToOne), `issued_at`, `certificate_number` (unique UUID slug), `vault_file` (FK to `vault.VaultFile`, nullable — PDF).

- `Cohort` — a time-bounded group running through a course together. Fields: `course`, `name`, `starts_at`, `ends_at`, `max_size`, `is_active`.

- `CohortMembership` — links a `Student` to a `Cohort`.

- `LearningPath` — a curated sequence of courses. Fields: `community`, `title`, `description`, `courses` (ordered M2M to `Course`).

- `LearningPathBadge` — badge awarded on path completion.

- `Script` / `ScriptSection` — extends `verbena.AbstractPage` / `AbstractSection`. A long-form narrative document attached to a course module (course notes, textbooks).

## Key coupling

- `memo.MemoDeck` — lessons of type `deck` embed a flashcard deck.
- `vault.VaultFile` — certificates are stored as vault files.
- `assets.LedgerAccount` — paid courses debit the buyer's account.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `competence` — SkillBadge unlocked on module completion
- `memo` — MemoDeck embedded in deck-type lessons
- `palimpsest` — Page attached as course notes/textbook
- `quizzes` — Quiz attached to modules; Certificate links to quiz result
- `vault` — Certificate PDFs stored as VaultFile
- `verbena` — Script/ScriptSection extend AbstractPage/AbstractSection────────────────────────────────────────────────────────────────────────



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
  APP: toto.assembly
========================================================================

# toto.assembly

Democratic governance engine. Communities legislate through proposals, votes, and enacted decisions. Passed decisions can create `CommunityRule`, `CommunityTransactionFee`, or `PollTax` records that other parts of the system read to enforce policy.

## Purpose

Any community member can submit a proposal. The community votes within a configurable window; if quorum and threshold are met, an `AssemblyDecision` is created and the relevant policy object is enacted. An optional `CommunitySenate` can veto a passed proposal within a deadline window. Every decision is hash-chained to the previous one — the governance history is tamper-evident. This is how the community sets its own rules, fees, and taxes without needing an administrator.

## Models

- `CommunityAssemblyConfig` — per-community governance parameters. Fields: `community` (OneToOne), `quorum_percent`, `pass_threshold_percent`, `voting_period_days`, `senate_veto_window_hours`, `allow_external_proposals`.

- `AssemblyProposal` — a proposal submitted for community vote. Fields: `community`, `proposer` (FK to `people.Person`), `type` (enum: `rule_change`, `fee_change`, `tax_change`, `emg_declare`, `custom`), `title`, `body`, `status` (`draft → open → passed / failed / vetoed`), `voting_opens_at`, `voting_closes_at`, `metadata` (JSON carries proposed values).

- `AssemblyVote` — a single member's vote on a proposal. Fields: `proposal`, `voter` (FK to `people.Person`), `choice` (`yes / no / abstain`), `cast_at`. Unique on `(proposal, voter)`.

- `AssemblyDecision` — immutable record of a passed or failed proposal. Fields: `proposal` (OneToOne), `outcome` (`passed / failed / vetoed`), `decided_at`, `hash` (SHA-256 of decision content), `previous_hash` (chain link). Hash-chained — tampering with any decision breaks the chain.

- `CommunityRule` — a policy rule enacted by a decision. Fields: `community`, `decision` (FK), `rule_type` (slug), `parameters` (JSON), `is_active`, `effective_from`, `expires_at`.

- `CommunityTransactionFee` — a fee levied on marketplace transactions in a community. Fields: `community`, `decision`, `fee_type` (`flat / percentage`), `amount` or `rate`, `asset`, `fee_account` (FK to `assets.LedgerAccount`), `applies_to` (product category filter), `is_active`.

- `PollTax` — a periodic membership levy. Fields: `community`, `decision`, `asset`, `amount_base_units`, `period` (`daily / weekly / monthly`), `tax_account`, `is_active`, `next_collection_at`.

- `PollTaxPayment` — record of a single poll-tax collection from one member. Fields: `poll_tax`, `payer` (FK to `people.Person`), `ledger_transaction`, `period_label`, `paid_at`.

- `CommunitySenate` — an optional upper house with veto power. Fields: `community` (OneToOne), `members` (M2M to `people.Person`), `is_active`.

- `SenateVeto` — a senate veto of a passed proposal, within the veto window. Fields: `proposal`, `vetoed_by` (FK to `people.Person`), `reason`, `vetoed_at`.

## Decision hash chain

Every `AssemblyDecision` stores a SHA-256 hash of its content plus the `previous_hash` of the preceding decision. This forms an append-only ledger of governance outcomes — the same tamper-evidence pattern used in `assets.LedgerHash`.

## Key coupling

- `mobilization.EmergencyStatus.source_proposal` — an `AssemblyProposal` of type `emg_declare` must pass before an emergency can be activated.
- `bazaar` reads `CommunityTransactionFee` records at checkout to compute fees.
- `assembly` creates `CommunityRule` and `PollTax` records that the rest of the system enforces.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — CommunityTransactionFee references LedgerAccount; PollTax uses Asset
- `people` — AssemblyProposal.proposer, AssemblyVote.voter, SenateVeto.vetoed_by
- `socialhub` — Every proposal and config is community-scoped────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.assets
========================================================================

# toto.assets — Lightweight Asset Ledger

A native Django/PostgreSQL asset ledger inspired by Algorand Standard Assets (ASA).
No blockchain libraries, no Algorand SDK, no smart contracts.

## Purpose

An admin mints an `Asset` (ticker, decimals, total supply). Users hold balances as `AssetHolding` records. Every transfer, mint, or burn posts an immutable `LedgerEntry` pair (debit + credit) under a `LedgerTransaction`. Once posted, the transaction is sealed; corrections go through an explicit reversal. A SHA-256 `LedgerHash` chain links every posted transaction — tampering with any entry breaks the chain and can be detected by `verify_hash_chain()`. `Obligation` records model debts that will be settled via future transactions. `Contract` / `Agreement` are the runtime layer for Lapis smart contracts executed by financial instruments.

## Concepts

| ASA concept | This ledger |
|---|---|
| Asset | `Asset` model |
| Account | `LedgerAccount` model |
| Asset holding | `AssetHolding` model |
| Transaction | `LedgerTransaction` + `LedgerEntry` rows |
| Clawback / freeze | Not implemented |

## Amounts

All amounts are stored as **integer base units** internally, exactly like Algorand.

```python
display_amount = Decimal("12.34")
decimals = 2
base_units = 1234  # what is stored
```

Helpers:
```python
from toto.assets.models import to_base_units, from_base_units

to_base_units(Decimal("12.34"), 2)  # → 1234
from_base_units(1234, 2)            # → Decimal("12.34")
```

Never use `float`.

## Ledger entries

Each transaction produces balanced `LedgerEntry` rows:

- Positive `amount_base_units` → account **receives** units
- Negative `amount_base_units` → account **sends** units

The sum of all entries per asset across all transactions is always zero.
Entries are **immutable** — they cannot be edited or deleted after creation.

## Corrections

Posted transactions are immutable. To correct a mistake, call `reverse_transaction()`.
This creates a new `LedgerTransaction` with `transaction_type="reversal"` and opposite entries.

## Hash chain

Every posted transaction gets a `LedgerHash` record containing:
- SHA-256 hash of the transaction data + entries
- Previous hash (links records into a chain)

This provides tamper evidence. Call `verify_hash_chain()` to re-verify the entire chain.

## Swapping the engine

To replace the Django backend (e.g., with Algorand in the future):

1. Subclass `toto.assets.backend.LedgerBackend`
2. Override `create_asset`, `transfer_asset`, `reverse_transaction`
3. Set `ASSETS_BACKEND = "myapp.MyBackend"` in settings

```python
from toto.assets.backend import get_backend

backend = get_backend()
asset = backend.create_asset(name="Token", ...)
```

## Quick example

```python
from decimal import Decimal
from toto.assets.models import LedgerAccount
from toto.assets.backend import get_backend

reserve = LedgerAccount.objects.create(code="reserve", name="Reserve", account_type="reserve")
alice   = LedgerAccount.objects.create(code="alice",   name="Alice",   account_type="user")
bob     = LedgerAccount.objects.create(code="bob",     name="Bob",     account_type="user")

backend = get_backend()

asset = backend.create_asset(
    name="Spectrum Credit", unit_name="SPC",
    total_supply=Decimal("1000000"), decimals=2,
    reserve_account=reserve, reference="create-spc",
)

tx = backend.transfer_asset(
    asset=asset, sender_account=reserve, receiver_account=alice,
    amount=Decimal("100.00"), reference="txfr-spc-alice-001",
)

backend.reverse_transaction(
    transaction=tx, reference="rev-txfr-spc-alice-001",
    description="Mistaken transfer reversal",
)
```

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `inventory` — Physical asset types linked via Contract/Agreement runtime
- `people` — Person as account holder identity────────────────────────────────────────────────────────────────────────



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
  APP: toto.bazaar
========================================================================

# toto.bazaar

Community marketplace. Handles shops, product catalogs, cart → order → payment flows, inventory, shipping, and coupons. Payments are settled through the `assets` ledger.

## Purpose

A community opens a `Shop`, vendors list `Product` records, and buyers browse → add to cart → checkout. Payment settles as a ledger transfer or creates an `Obligation` for credit terms. `MarketCustodian` records let a regulated body approve products before they go live and review service deliveries before payment is released. Community transaction fees (set by assembly vote) are applied automatically at checkout. Disputes over orders feed into the `tribunal`.

## Models

- `Shop` — a community storefront. FK to `socialhub.Community`. Has `is_active`, `slug`, M2M to `Person` (managers).
- `Vendor` — a supplier within a shop. FK to `Shop` and `people.Person`.
- `ProductCategory` — hierarchical tree (self-referential parent FK).
- `Product` — a listing. Fields: `shop`, `vendor`, `category`, `name`, `description`, `price_base_units`, `price_asset` (FK to `assets.Asset`), `stock_quantity`, `product_type` (`physical / service / digital`), `is_active`. Has `ProductImage` and `ProductVariant` children.
- `ProductVariant` — size/color/option variant of a product with its own price override.
- `InventoryMovement` — ledger of stock changes (`restock / sale / adjustment / return`).
- `Cart` / `CartItem` — session-scoped pre-order container.
- `Order` — a confirmed purchase. Fields: `shop`, `buyer` (FK to `people.Person`), `status` (`pending / paid / processing / shipped / delivered / cancelled / refunded`), `total_base_units`, `currency_asset`, `payment_method` (`ledger / obligation / cash`).
- `OrderItem` — line items on an order.
- `ServiceDelivery` — delivery record for service-type products. Has a `completed_at` and `delivery_notes`.
- `OrderStatusEvent` — append-only status history for an order.
- `PaymentIntent` — tracks a payment attempt. FK to `Order`. Has `status` and `metadata`.
- `PaymentTransaction` — links an `Order` to an `assets.LedgerTransaction`.
- `ShippingMethod` / `Shipment` — shipping configuration and per-order shipment tracking.
- `Coupon` — discount code with `discount_type` (`flat / percentage`), usage limit, expiry.
- `OrderDiscount` — links a `Coupon` to an `Order`.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `get_or_create_cart(request, shop)` | Returns or creates a cart for the session |
| `add_product_to_cart(request, product, ...)` | Adds/updates a cart item; validates stock |
| `recalculate_cart(cart)` | Recomputes subtotal, fees, and coupon discounts |
| `validate_cart(cart)` | Checks stock, active status, and required fields |
| `create_order_from_cart(cart, checkout_data)` | Converts cart to `Order`; locks inventory |
| `reserve_inventory(order)` | Creates `InventoryMovement(sale)` records |
| `mark_order_paid(order, payment_data)` | Sets `paid`; triggers `PaymentTransaction` |
| `pay_order_with_ledger(order, buyer_account, asset)` | Posts ledger entries for payment |
| `create_obligation_for_order(order, ...)` | Creates `assets.Obligation` for credit-term orders |
| `apply_coupon(cart, code)` | Validates and applies coupon |
| `product_unit_price(product, variant, currency)` | Returns display price in target currency |

## Key coupling

- `assets.LedgerAccount` — buyer and seller accounts for ledger payments.
- `assembly.CommunityTransactionFee` — bazaar reads community fee records and applies them at checkout.
- `inventory.RealWorldObject` — physical products can link to inventory items.
- `tribunal.TribunalCase` — cases can be linked to a disputed order.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — Payments post to LedgerAccount; fees reference LedgerAccount
- `locations` — Shipping addresses; shop location
- `people` — Vendor and buyer are Person records
- `socialhub` — Every shop is community-scoped────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.bento
========================================================================

# toto.bento

Idea management and innovation pipeline. Organizes ideas into categorized boxes with directed links between them, supporting concept mapping and ideation workflows.

## Purpose

Community members capture ideas as `IdeaBox` records and draw directional relationships between them (`builds_on`, `contradicts`, `leads_to`). Ideas move through a status pipeline from raw idea → exploring → validated → implementing. The resulting graph of linked ideas forms a visual concept map that the community can use to prioritize and track innovation initiatives.

## Models

- `Category` — extends `DomainEntity`. A classification for idea boxes. Fields: `name`, `slug`, `community` (FK), `color`, `icon`.

- `IdeaBox` — extends `DomainEntity`. A named idea or concept. Fields: `title`, `description`, `category` (FK), `community` (FK), `author` (FK to `people.Person`), `status` (`idea / exploring / validated / implementing / archived`), `is_public`, `tags` (M2M), `score` (computed from links/votes).

- `IdeaLink` — a directed relationship between two boxes. Fields: `from_box` (FK to `IdeaBox`), `to_box` (FK to `IdeaBox`), `link_type` (`builds_on / contradicts / related / leads_to`), `description`, `author` (FK to `people.Person`). Unique on `(from_box, to_box, link_type)`.

## Key coupling

- Community-scoped. Standalone from financial/governance systems.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — IdeaBox.author and IdeaLink.author
- `socialhub` — Community-scoped categories and idea boxes────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.bourse
========================================================================

# toto.bourse

Peer-to-peer asset exchange requests. Members offer to exchange a quantity of one asset for another at a specified rate. Matching and settlement go through the `assets` ledger.

## Purpose

A member posts an `AssetExchangeRequest` offering X units of asset A for Y units of asset B. Other members browse open requests and accept one. On acceptance, the service validates both parties have sufficient balances and posts the two-sided ledger swap atomically. This is the OTC (over-the-counter) desk — no order book, no price discovery, just bilateral offers.

## Models

- `AssetExchangeRequest` — an offer to exchange assets. Fields:
  - `requester` (FK to `people.Person`)
  - `offer_asset` (FK to `assets.Asset`), `offer_amount_base_units`
  - `request_asset` (FK to `assets.Asset`), `request_amount_base_units`
  - `offer_account` / `request_account` (FKs to `assets.LedgerAccount`)
  - `status` — `open / accepted / rejected / cancelled / expired`
  - `counterparty` (FK to `people.Person`, nullable — set on acceptance)
  - `response_note`
  - `expires_at`, `created_at`, `updated_at`

## Services (`services.py`)

| Function | What it does |
|---|---|
| `accept_exchange_request(exchange_request, counterparty_account, response_note)` | Validates both accounts have sufficient balance; posts ledger transactions for both sides; sets `status=accepted` |
| `reject_exchange_request(exchange_request, response_note)` | Sets `status=rejected` |
| `cancel_exchange_request(exchange_request, response_note)` | Sets `status=cancelled` (requester-initiated) |

## Key coupling

- `assets.Asset`, `assets.LedgerAccount` — exchange uses ledger accounts for both sides of the trade.
- `people.Person` — requester and counterparty.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — Asset and LedgerAccount for both sides of exchange
- `people` — requester and counterparty Person FKs────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.claims
========================================================================

# toto.claims

Contract lifecycle primitives. The four models here are the runtime objects that Lapis smart contracts and financial instruments create and track. They attach to an `assets.Agreement` or `assets.Contract` and are the primary source of `ContractEvent` records.

## Purpose

When a financial instrument or Lapis contract executes, it doesn't just post ledger entries — it creates structured state: an `Entitlement` grants a right, a `Schedule` schedules future billings, a `Condition` gates an effect, an `Allocation` ring-fences funds. Every significant state change appends a `ContractEvent` to the audit log. This gives contracts a queryable lifecycle history — you can always answer "what happened under this agreement, and when."

## Models

- `Entitlement` — a right held by a `LedgerAccount`. Kinds: `service_access`, `lease_right`, `exercise_right`, `reward_eligibility`, `claim_right`, `usage_right`. Fields: `agreement` / `contract` FK, `holder_account`, `kind`, `status` (`active / suspended / expired / revoked`), `starts_at`, `ends_at`, `metadata`.

- `Schedule` — a temporal trigger for billing, renewal, vesting, payout, settlement, reward, or checkpoint events. Fields: `agreement` / `contract` FK, `kind`, `status` (`active / paused / completed / cancelled`), `frequency` (`once / daily / weekly / monthly / quarterly / annual / custom`), `next_run_at` (indexed), `last_run_at`, `run_count`, `max_runs` (nullable), `cron_expression` (for custom frequency).

- `Condition` — a predicate that gates an effect. Kinds: `time`, `status`, `approval`, `balance`, `evidence`, `threshold`, `manual`, `external`. Fields: `agreement` / `contract` FK, `kind`, `status` (`pending / satisfied / failed / waived`), `expression` (JSON — encodes the rule), `evaluated_at`, `evaluator` (FK to `people.Person`, for manual/approval kinds).

- `Allocation` — a ring-fenced asset reserve. Kinds: `escrow_hold`, `collateral`, `margin`, `vesting_pool`, `staking_lock`, `prepaid_balance`, `budget`. Fields: `agreement` / `contract` FK, `asset` (FK to `assets.Asset`), `account` (FK to `assets.LedgerAccount`), `kind`, `status` (`active / released / consumed / cancelled`), `amount_base_units`, `allocated_base_units`, `released_base_units`, `consumed_base_units`.

- `ContractEvent` — append-only audit log of contract lifecycle events. Fields: `agreement` / `contract` FK, `kind` (rich enum: `payment_due`, `payment_paid`, `entitlement_granted`, `condition_satisfied`, `allocation_released`, `default`, `settlement`, etc.), `transaction` (FK to `assets.LedgerTransaction`, nullable), `obligation`, `entitlement`, `schedule`, `condition`, `allocation` (all nullable FKs), `metadata`, `occurred_at`.

## Key coupling

- `instruments` — all 10 instrument types create and update `Entitlement`, `Schedule`, `Condition`, `Allocation`, and `ContractEvent` records through their service classes.
- `assets.Agreement` — the runtime instance of a contract; most claims objects FK here.
- A Celery task polls `Schedule.next_run_at` hourly to process due schedules.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — Entitlement and Allocation reference Asset and LedgerAccount────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.competence
========================================================================

# toto.competence

Skills registry. Defines skill groups and named skill badges that practitioners and responders can earn. Supports skill prerequisites.

## Purpose

`SkillBadge` records are the credential unit of the platform. `academy` awards them when students pass module exams. `mobilization` links them to `ResponderSkill` records to track certified responder proficiencies. `kanban` tasks can declare required skills in metadata. `SkillBadgePrerequisite` enforces learning order — you can't earn "Advanced First Aid" without "Basic First Aid" first.

## Models

- `Experience` — a named experience/qualification type. Fields: `name`, `slug`, `description`. Reference data.

- `SkillGroup` — a category of skills. Fields: `name`, `slug`, `description`, `icon`. Examples: `mobilization`, `technical`, `leadership`.

- `SkillBadge` — a single skill within a group. Fields: `group` FK, `name`, `slug`, `description`, `icon`, `level` (`basic / intermediate / advanced / expert`), `is_active`.

- `SkillBadgePrerequisite` — a directed prerequisite link between two badges. Fields: `badge` FK, `prerequisite` FK (to `SkillBadge`). Unique on `(badge, prerequisite)`. Prevents self-referential prerequisites via `clean()`.

## Key coupling

- `mobilization.ResponderSkill` — each responder skill links to a `SkillBadge` with a verified proficiency level.
- `kanban.Task` metadata — tasks can specify required skills (via `skill_metadata()` in `detections.services`).
- Skills are seeded per-group by ingress commands (e.g. `ingress_mobilization` seeds 7 mobilization skills).

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — Experience and SkillBadge are awarded to Person records────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.contracts
========================================================================

# toto.contracts

Contract graph editor. Provides a human-authored, visual layer on top of `assets.Contract` and `claims` primitives. Contracts are represented as labeled directed graphs (nodes + edges) rendered with Cytoscape.js.

## Purpose

A lawyer or contract author creates a `Contract` document and builds a graph of nodes (obligations, entitlements, accounts, events) connected by typed edges (grants, creates_duty, triggers, settles…). Nodes can be manually authored prose or backed by real Django model instances via a generic FK. The graph is serialized to Cytoscape.js format for interactive visual editing in the browser. This layer is the human-readable face of what `assets.Contract` executes.

## Models

- `Contract` — a named contract document. Fields: `uuid`, `name` (unique), `description`, `code` (optional YAML snapshot — validated as `language: lapis`, `kind: claims_mesh`), `metadata`. The `contracts.Contract` is the visual/document layer; `assets.Contract` is the executable VM layer.
- `ContractNode` — a node in the contract graph. Fields: `contract` FK, `key` (slug, stable ID within this contract), `node_type` (14 supported types: `contract`, `obligation`, `entitlement`, `schedule`, `condition`, `allocation`, `event`, `ledger_account`, `asset`, `ledger_transaction`, `resource_type`, `agreement`, `manual`, `note`), `title`, `description`, `is_manual` (manually authored vs backed by a real object), `object_app` / `object_model` / `object_id` (generic FK to any Django model), `position_x` / `position_y` (Cytoscape layout coords). Unique on `(contract, key)`.
- `ContractEdge` — a directed relationship between two nodes. Fields: `contract`, `source` (FK to `ContractNode`), `target` (FK to `ContractNode`), `edge_type` (20 supported types including `grants`, `creates_duty`, `triggers`, `gates`, `requires`, `allocates`, `secures`, `records`, `settles`, `debtor`, `creditor`, `uses_asset`, `composes`), `label`, `description`. `clean()` validates both nodes belong to the same contract.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `create_node(contract, key, node_type, ...)` | Validates type, creates `ContractNode` |
| `create_manual_node(contract, key, ...)` | Creates a node with `is_manual=True` |
| `create_edge(contract, source_key, target_key, edge_type, ...)` | Validates edge type and node membership; creates `ContractEdge` |
| `contract_to_cytoscape(contract)` | Serializes contract graph to Cytoscape.js element format for the frontend graph editor |

## Key coupling

- Nodes can back any domain object via the `(object_app, object_model, object_id)` generic FK — e.g. a node can represent a real `assets.Obligation` record.
- `ravioli` — the graph YAML exported from a contract can be imported as a Neo4j subgraph via `toto/ravioli/graph/mobilization.yaml` / `response.yaml` style manifests.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



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

## Key coupling

- `core.Platform` is read at boot time by `sso_master.services.get_active_platform()` to resolve the OIDC issuer URL.
- `backup.BackupProfile` has a one-to-one with `Platform`.
- `Theme` is read by every template that renders the platform's branding.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.detections
========================================================================

# toto.detections

Incident and threat detection registry. Records detected events — hazards, incidents, threats — with geographic location, severity, and status. Feeds the mobilization pipeline and the kanban task system.

## Purpose

A community member (or an automated sensor feed) files a `Detection`. The detection is geo-tagged, categorized, and rated for severity. A `DetectionHandle` assigns a responder to work it. If the detection is serious enough, it is linked as evidence to a `MobilizationReport`, whose severity is recalculated from the weighted scores of all attached detections. Once a mobilization event is activated, field `Intervention` records link back to the detection they are mitigating — creating a full chain: detection → report → event → deployment → intervention.

## Models

- `DetectionCategory` — extends `DomainEntity`. Hierarchical category tree (self-referential `parent` FK). Examples: Natural Disaster > Flood, Security > Intrusion.

- `Detection` — extends `EventBase` (which extends `DomainEntity`). The core record. Key fields:
  - `category` — FK to `DetectionCategory`
  - `address`, `zone`, `route` — FKs to `locations.*` (geographic anchors)
  - `reported_by` — FK to `people.Person`
  - `involved_persons` — M2M to `people.Person`
  - `mitigation_task` — FK to `kanban.Task` (the task designated to resolve this detection)
  - `severity` — `low / medium / high / critical`
  - `detection_type` — `incident / hazard / threat / observation`
  - `status` — `new / acknowledged / in_progress / mitigated / closed / false_positive`
  - `severity_score` (float, calculated from weighted evidence) — used by `mobilization` for report severity

- `DetectionHandle` — extends `DomainEntity`. An assignment of a person to work a detection. Fields: `detection` FK, `assigned_to` (FK to `people.Person`), `status` (`open / in_progress / resolved / transferred`), `notes`, `opened_at`, `closed_at`.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `create_detection_help_task(detection, owner, reviewer)` | Creates a `kanban.Task` and links it as `detection.mitigation_task` |
| `ensure_detection_mitigation_task(detection, ...)` | Idempotent: creates the task only if not already linked |
| `detection_map_feature(detection)` | Returns a GeoJSON feature dict for map rendering |
| `skill_metadata(required_skills)` | Builds skill metadata dict for task creation |

## Key coupling

- `mobilization.MobilizationReportEvidence` — detections are attached as evidence to mobilization reports. Severity scores flow upward to recalculate report severity.
- `response.Intervention.detection` — an intervention tracks which detection it mitigates.
- `kanban.Task` — `mitigation_task` FK creates a direct link between a detection and its project-management resolution.
- `locations` — geographic anchors for map display.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `events` — Detection can link to a ScheduledEvent
- `people` — Detection reporter and handler are Person records────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.enigma
========================================================================

# toto.enigma

*(Studio only — requires BUILD_STUDIO=1)*

Real-time chat over WebSockets. Rooms hold participants; messages are delivered via Django Channels consumers backed by a Redis channel layer.

## Purpose

Community members connect to `ws://.../ws/enigma/{room_slug}/` and exchange messages in real time. Messages are ephemeral — they are relayed through the Redis channel layer but not persisted to the database. `Room` records define the chat spaces; `Participant` records gate who can access a room. Rooms can be community-scoped (for community channels) or platform-wide (for cross-community coordination).

## Models

- `Room` — a chat space. Fields: `name`, `slug` (unique), `community` (FK to `socialhub.Community`, nullable — can be community-scoped or platform-wide), `is_private`, `created_by` (FK to `people.Person`), `created_at`.

- `Participant` — a person's membership in a room. Fields: `room` FK, `person` (FK to `people.Person`), `joined_at`, `last_read_at`, `is_admin`. Unique on `(room, person)`.

Messages are not persisted to the database — they are relayed ephemerally through the Redis channel layer. If message history is needed it must be added separately.

## WebSocket protocol

- Connect to `ws://.../ws/enigma/{room_slug}/`
- The Channels consumer authenticates the session and places the socket into a Redis channel group named `enigma_{room_slug}`.
- Incoming messages are broadcast to all members of the group.

## Key coupling

- `channels_redis.core.RedisChannelLayer` — required at runtime (studio mode only).
- `socialhub.Community` — optional community scoping for rooms.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — Participant.person FK
- `socialhub` — Rooms are community-scoped────────────────────────────────────────────────────────────────────────



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
  APP: toto.gervazy
========================================================================

# toto.gervazy

Encryption-at-rest vault. Implements a three-tier AES-256-GCM key hierarchy: password → Argon2id KDF → UKEK → wrapped VMK → wrapped DEK → encrypted objects. Nothing sensitive is stored in plaintext.

## Purpose

When a user sets their vault password, Argon2id derives a UKEK from it (never stored). The UKEK encrypts a `VaultMasterKey` blob. The VMK in turn wraps `WrappedDataKey` records (one per namespace). Data keys encrypt the actual secrets, files, and private keys. Decryption requires the user's password at runtime — the system cannot read stored secrets without it. The `CryptoAuditLog` records every operation for compliance. The OIDC signing key (`sso_master`) and all outbound API credentials (`api`) live in gervazy.

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

- `UserStrongbox` — per-user container. Links `auth.User` to Argon2id salt and one or more `VaultMasterKey` records. Has a `is_locked` flag.
- `VaultMasterKey` — the VMK blob for a strongbox. Fields: `encrypted_vmk` (hex), `kdf_salt`, `kdf_params` (JSON — memory, time, parallelism), `is_active`.
- `WrappedDataKey` — a namespace-scoped DEK wrapped under a VMK. Fields: `namespace` (slug, e.g. `"sso_signing"`), `encrypted_dek` (hex), `vault_master_key` FK.
- `EncryptedSecret` — an arbitrary key/value secret stored encrypted. Fields: `strongbox` FK, `wrapped_data_key` FK, `name`, `namespace`, `encrypted_value` (hex), `iv` (hex).
- `EncryptedFile` — an encrypted file object. Fields: `strongbox`, `wrapped_data_key`, `original_filename`, `encrypted_content` (stored in chunks via `EncryptedFileChunk`), `content_type`, `size_bytes`.
- `EncryptedFileChunk` — a sequential chunk of an `EncryptedFile`. Fields: `file` FK, `chunk_index`, `encrypted_data` (hex).
- `EncryptedPrivateKey` — an RSA/EC private key stored encrypted. Fields: `strongbox`, `wrapped_data_key`, `algorithm`, `key_size`, `encrypted_pem` (hex), `public_key_pem` (plaintext, safe to store).
- `CryptoAuditLog` — append-only log of vault operations (`unlock`, `wrap`, `unwrap`, `encrypt`, `decrypt`, `key_rotation`). Fields: `user` FK, `operation`, `namespace`, `target_id`, `ip_address`, `success`, `error_message`.

## Key coupling

- `sso_master.SSOSigningKey.encrypted_key` → `gervazy.EncryptedPrivateKey` — the OIDC signing key lives here.
- `backup.BackupProfile.signing_key` → `gervazy.EncryptedPrivateKey` — backup signing key.
- `api.ApiConnector.api_secret`, `api.EmailService.smtp_secret` → `gervazy.EncryptedSecret` — outbound API credentials.
- The vault is unlocked per-request using `SSO_VAULT_PASSWORD` (sso_master) or interactive password input for user vaults.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



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
  APP: toto.instruments
========================================================================

# toto.instruments

## Purpose

Financial instruments wrap the `assets` ledger primitives into higher-level financial contracts. Each instrument type creates the appropriate `claims.Entitlement`, `Schedule`, `Condition`, `Allocation`, and `ContractEvent` records as it progresses through its lifecycle. Service classes (one per type) encapsulate all state transitions — views never touch model fields directly. Instruments are how communities structure real economic relationships: a vendor gets paid through escrow, a contributor vests equity, a subscriber is billed monthly, a borrower amortizes a loan.

Django app for deterministic financial instruments:

- Escrow
- Forward contracts
- Future markets and future contracts
- Future margin positions
- Revenue share contracts
- Timelocks
- Vesting contracts
- Staking positions
- Instrument obligations and execution audit log

Loans and insurance are intentionally excluded. Put them in `toto.risk`, because they require credit scoring, underwriting, claim review, and risk decisions.

## Install

Copy `instruments/` to `toto/instruments/` and add the app:

```python
INSTALLED_APPS = [
    ...,
    "toto.instruments",
]
```

Include URLs:

```python
path("instruments/", include("toto.instruments.urls")),
```

Run migrations:

```bash
python manage.py makemigrations instruments
python manage.py migrate
```

## Recommended assets patch

In `toto.assets.models.AccountType`, add:

```python
CONTRACT = "contract", "Contract"
```

Use contract accounts for escrows, margin pools, staking pools, timelock vaults, and vesting pools.

In `TransactionType`, consider adding generic contract transaction types:

```python
CONTRACT_LOCK = "contract_lock", "Contract Lock"
CONTRACT_RELEASE = "contract_release", "Contract Release"
CONTRACT_SETTLEMENT = "contract_settlement", "Contract Settlement"
CONTRACT_PAYOUT = "contract_payout", "Contract Payout"
```

Optionally extend `Obligation` with:

```python
source_type = models.CharField(max_length=100, blank=True)
source_id = models.CharField(max_length=255, blank=True)
metadata = models.JSONField(default=dict, blank=True)
```

The services in this app detect those optional `Obligation` fields and will still work if they are absent.

## Architecture

`assets` remains the settlement and accounting layer. This app never edits `AssetHolding` directly. All asset movement goes through `get_backend().transfer_asset(...)`.

`instruments` defines deterministic financial contracts and records their execution history.

`risk` should hold loans, insurance, credit checks, underwriting decisions, and claims.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — Financial instruments operate on Asset and LedgerAccount────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.inventory
========================================================================

# toto.inventory

Physical asset registry. Tracks real-world objects from acquisition through lifecycle events. Objects can be tokenized on the ledger via `assets.Tokenization`.

## Purpose

A community catalogs its physical property — vehicles, tools, equipment, real estate — as `RealWorldObject` records. Items move through condition and status stages over time. Tokenizing an object issues an on-ledger `Asset` that represents it, enabling ownership transfer and collateralization through the financial system. During emergencies, inventory items can be requisitioned for deployment use via `EmergencyEquipmentAccess`; during normal operations they are allocated to specific deployments via `DeploymentEquipment`.

## Models

- `ObjectType` — extends `DomainEntity`. Classification for real-world objects (e.g. "Vehicle", "Medical Equipment", "Tool"). Has `category` slug and `unit_of_measure`.

- `RealWorldObject` — extends `DomainEntity`. The core record. Key fields:
  - `object_type` — FK to `ObjectType`
  - `owner` — FK to `socialhub.Community` (nullable; community-owned items)
  - `custodian` — FK to `people.Person` (nullable; person currently responsible)
  - `serial_number`, `model_name`, `manufacturer`
  - `acquisition_date`, `acquisition_cost_base_units`, `cost_asset`
  - `condition` — `new / good / fair / poor / damaged / decommissioned`
  - `status` — `in_service / in_storage / in_transit / maintenance / decommissioned`
  - `storage_location` — FK to `StorageLocation`
  - `notes`

- `InventorySite` — extends `DomainEntity`. A physical facility that holds objects. Fields: `community` FK, `address` FK to `locations.Address`, `site_type` (`warehouse / depot / field_station / vehicle`), `is_active`.

- `StorageLocation` — extends `DomainEntity`. A named position within a site (shelf, bay, room). Fields: `site` FK, `code`, `capacity`.

## Key coupling

- `assets.Tokenization` — links a `RealWorldObject` one-to-one to an `assets.Asset`. Permanent record; delete blocked.
- `response.DeploymentEquipment` — inventory items are allocated to deployments.
- `mobilization.EmergencyEquipmentAccess` — items can be granted hybrid access under emergencies.
- `bazaar.Product` — physical products may reference an inventory item.
- `tribunal.TribunalCase` — cases can concern a specific `RealWorldObject`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — RealWorldObject can be tokenized as an Asset
- `locations` — InventorySite address and StorageLocation
- `people` — Object ownership and custodian Person FKs────────────────────────────────────────────────────────────────────────



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
- `verbena` — DocumentationPage extends AbstractPage────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.library
========================================================================

# toto.library

Media and reference library. Stores books, articles, audio references, and video references. Items can be organized into collections and attached to vault files.

## Purpose

Academy content authors and researchers catalog reference material — textbooks, papers, recordings, lectures — as typed `LibraryItem` subclass records. Items carry vault file attachments (PDFs, audio files) and can be tagged with `memo.Tag`. `LibraryCollection` lets a curator bundle items into a named reading/viewing list for a course or community.

## Models

- `LibraryItem` — abstract base (extends `DomainEntity`). Common fields: `title`, `description`, `tags` (M2M to `memo.Tag`), `vault_file` (FK to `vault.VaultFile`, nullable), `community` (FK), `is_public`, `author_name`, `published_at`.

- `Book` — extends `LibraryItem`. Adds: `isbn`, `publisher`, `edition`, `page_count`, `language`.

- `Article` — extends `LibraryItem`. Adds: `journal`, `doi`, `url`, `abstract`.

- `AudioReference` — extends `LibraryItem`. Adds: `duration_seconds`, `format` (`mp3 / wav / ogg / flac`), `url`.

- `VideoReference` — extends `LibraryItem`. Adds: `duration_seconds`, `platform` (`youtube / vimeo / internal / other`), `url`, `embed_code`.

- `LibraryCollection` — a named grouping of items. Fields: `name`, `description`, `community` FK, `books` / `articles` / `audio` / `videos` (M2M to each type), `curator` (FK to `people.Person`), `is_public`.

## Key coupling

- `vault.VaultFile` — library items can attach their source file for download.
- `memo.Tag` — shared tagging system with the memo/flashcard app.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `palimpsest` — LibraryItem can link to a palimpsest Page
- `vault` — Audio/video references stored as VaultFile────────────────────────────────────────────────────────────────────────



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

- `people` — Territory and Zone can have a Person administrator────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.logistics
========================================================================

# toto.logistics

Transport and package tracking. Models a fleet of vehicles/carriers, packages in transit, and timestamped package events. Extends `DomainEntity`.

## Purpose

A logistics coordinator registers a `Transport` (vehicle or courier), then creates `Package` records for shipments in transit. As the package moves, `PackageEvent` records are appended — each event stamps a location and timestamp. A bazaar `Shipment` can be linked one-to-one to a `Package` for end-to-end order tracking. This app is independent of bazaar — communities can track non-commercial logistics (supply drops, equipment transfers) without a shop context.

## Models

- `TransportMode` — text choices: `road / rail / water / air / foot / mixed`.

- `Transport` — a carrier (vehicle, vessel, courier). Fields: `name`, `mode` (FK to `TransportMode`), `community` (FK), `capacity`, `is_active`, `current_location` / `origin` / `destination` (FKs to `locations.Address`), `operator` (FK to `people.Person`).

- `Package` — a shipment. Fields: `reference` (unique), `transport` (FK, nullable), `shipment` (OneToOne FK to `bazaar.Shipment`, nullable), `origin` / `destination` (FKs to `locations.Address`), `weight_kg`, `volume_cm3`, `status` (`pending / picked_up / in_transit / out_for_delivery / delivered / failed / returned`), `expected_at`, `delivered_at`.

- `PackageEvent` — append-only status event. Fields: `package`, `transport` (FK, nullable), `event_type` (`status_change / location_update / note`), `location` (FK to `locations.Address`, nullable), `note`, `occurred_at`.

## Key coupling

- `bazaar.Shipment` — each bazaar shipment can be tracked as a `Package` (OneToOne).
- `locations.Address` — origin, destination, and current location anchors.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `bazaar` — Package.shipment is OneToOne with bazaar.Shipment
- `locations` — origin, destination, and current_location are Address FKs────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.magistrate
========================================================================

# toto.magistrate

Community magistracy — a local enforcement and advisory layer between the assembly and the tribunal. Magistrates are appointed via assembly proposals and can issue decisions and fines.

## Purpose

A community votes (via `assembly`) to appoint a `Magistrate` to a named role. The magistrate can then issue `MagistrateDecision` records (advisories, directives, injunctions) and levy `MagistrateFine` penalties that create `assets.Obligation` records against the target's ledger account. Misconduct can be reported via `MagistrateReport`. `CommunityMagistrateSettings` configures the fine collection account and term limits. Magistracy sits between the legislative assembly and the judicial tribunal — it handles routine enforcement without needing a full jury.

## Models

- `MagistrateRole` — named role type (e.g. "Senior Magistrate", "Inspector"). Fields: `name`, `slug`, `authority_level` (int), `can_issue_fines` (bool), `can_issue_decisions` (bool).

- `Magistrate` — a `Person` appointed to a magistrate role in a community. Fields: `person` (FK), `role` (FK to `MagistrateRole`), `community` (FK), `status` (`active / suspended / retired`), `appointed_at`, `source_proposal` (FK to `assembly.AssemblyProposal` — the proposal that appointed them), `term_ends_at`.

- `MagistrateDecision` — a formal decision issued by a magistrate. Fields: `magistrate` FK, `community` FK, `title`, `body`, `decision_type` (`advisory / directive / injunction / ruling`), `status` (`draft / issued / appealed / overturned`), `issued_at`, `reviewed_by` (FK to `people.Person`), `related_case` (FK to `tribunal.TribunalCase`, nullable).

- `MagistrateReport` — a report filed about a magistrate (misconduct, review). Fields: `magistrate` FK, `reporter` (FK to `people.Person`), `summary`, `status` (`pending / reviewed / dismissed / escalated`), `acknowledged_by` (FK to `people.Person`), `acknowledged_at`.

- `CommunityMagistrateSettings` — per-community magistracy config. Fields: `community` (OneToOne), `max_magistrates`, `term_length_months`, `fine_collection_account` (FK to `assets.LedgerAccount`), `is_active`.

- `MagistrateFine` — a monetary fine issued by a magistrate under a decision. Fields: `decision` (OneToOne), `target_person` (FK), `target_account` (FK to `assets.LedgerAccount`), `asset` (FK), `amount_base_units`, `due_at`, `status` (`issued / paid / overturned / written_off`), `obligation` (FK to `assets.Obligation` — created on issuance), `overturned_by` (FK to `people.Person`).

## Key coupling

- `assembly.AssemblyProposal` — magistrates are appointed via assembly vote.
- `assets.Obligation` — fines create obligations against the target account.
- `assets.LedgerAccount` — fine collection account defined in settings.
- `tribunal.TribunalCase` — decisions can reference tribunal cases.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assembly` — MagistrateDecision can reference an AssemblyDecision
- `assets` — MagistrateFine denominated in Asset
- `people` — Magistrate and all parties are Person records
- `socialhub` — CommunityMagistrateSettings is community-scoped────────────────────────────────────────────────────────────────────────



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

Flashcard and diagram system. Supports spaced-repetition deck creation and inline diagram (Mermaid/Graphviz) storage. Decks are embedded in `academy` lessons.

## Purpose

Teachers build `MemoDeck` collections of `MemoCard` flashcards — each card has a front (prompt) and back (answer) plus optional difficulty rating and embedded diagram. Diagrams are stored as `MemoDiagram` records with raw source (Mermaid or Graphviz syntax) and cached SVG output. A `Lesson` in the academy is backed by a `MemoDeck` as its lecture content. Members can also create decks independently for personal study.

## Models

- `Tag` — simple tag model for memo decks. Fields: `name`, `slug`.

- `MemoDiagram` — a named diagram. Fields: `title`, `diagram_type` (`mermaid / graphviz / svg`), `source` (raw diagram source code), `rendered_svg` (cached SVG output), `author` (FK to `people.Person`), `created_at`.

- `MemoDeck` — a flashcard deck. Fields: `title`, `description`, `tags` (M2M), `author` (FK to `people.Person`), `is_public`, `community` (FK to `socialhub.Community`, nullable), `created_at`.

- `MemoCard` — a single flashcard within a deck. Fields: `deck` FK, `front` (question / prompt), `back` (answer / explanation), `order`, `difficulty` (`easy / medium / hard`), `diagram` (FK to `MemoDiagram`, nullable), `hint`, `tags` (M2M).

## Key coupling

- `academy.Lesson.memo_deck` — lessons of type `deck` embed a `MemoDeck` directly in the course module.
- `MemoDiagram` can be embedded in `MemoCard.diagram` to show visual content on the card back.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `vault` — MemoDiagram rendered SVG optionally stored as VaultFile────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.mobilization
========================================================================

# Mobilization App

Strategic command layer for emergency response. Handles the full upstream pipeline from detection to enacted event, including responder registry, emergency declarations, and campaign management. Field execution lives in the `response` app.

---

## How it works

```
Detection (detections app)
    │
    ▼
MobilizationReport  ──── evidence ────► MobilizationReportEvidence
    │  (draft → submitted → reviewed → enacted)
    │
    ▼
MobilizationEvent  ──── optional links ──► kanban.Campaign, events.ScheduledEvent
    │
    ├──► EmergencyStatus  ──► EmergencyEquipmentAccess
    │         (watch / warning / emergency / critical_emergency)
    │         requires AssemblyProposal vote before activation
    │
    └──► response.Deployment  (FK boundary — field operations continue in response app)
```

---

## Internal model relationships

### Reference / lookup models

| Model | Used by |
|---|---|
| `IncidentType` | `MobilizationReport.incident_type`, `MobilizationEvent.incident_type` |
| `AchievementBadge` | `PersonAchievement.badge` |

### Responder registry

```
people.Person ──OneToOne──► Responder ──M2M──► socialhub.Community
                                │
                                ├──FK (one-to-many)──► ResponderSkill ──FK──► competence.SkillBadge
                                ├──reverse FK──────────► response.DeploymentAssignment
                                └──reverse FK──────────► response.Intervention (assigned_to)
```

`Responder` wraps a `Person` and tracks operational state (`current_status`). Status transitions are managed exclusively through `services.py` — never set directly. Transitions are triggered by field-side operations in `response` (via `services.activate_deployment_assignment`, `services.release_responder_from_deployment`, `services.complete_deployment`).

**Eligibility constraint** — `Responder.clean()` enforces that a person must be either `is_federal_agent=True` or a member of at least one `Community` with `is_federal_tribe=True`.

`PersonAchievement` awards an `AchievementBadge` to a `Person`, optionally tied to a `response.Deployment`. The `unique_together` on `(person, badge)` means each badge can only be awarded once per person.

### Report pipeline

```
MobilizationReport
    ├──FK──► socialhub.Community
    ├──FK──► IncidentType
    ├──FK──► people.Person  [submitted_by, reviewed_by, enacted_by]
    └──reverse FK──► MobilizationReportEvidence (evidence_links)
                          │
                          └──FK──► detections.Detection
```

Severity on `MobilizationReport` is recalculated automatically by `update_report_severity_from_evidence()` whenever evidence is added or changed. The algorithm:

1. For each linked detection: `score += (detection.severity_score + role_bump) × weight_factor`
   - `evidence_role` bumps: `primary +1`, `contradictory -1`, others `0`
   - `weight` factors: `high=1.5`, `normal=1.0`, `low=0.5`
2. `avg = total_score / evidence_count`
3. Thresholds: `≥2.5 → critical`, `≥1.8 → high`, `≥0.8 → medium`, else `low`

`unique_together` on `(report, detection)` — each detection can only be linked once per report.

### Event and emergency

```
MobilizationEvent
    ├──FK──► socialhub.Community
    ├──FK──► MobilizationReport  (source_report — must share community)
    ├──FK──► events.ScheduledEvent
    ├──FK──► kanban.Campaign  (campaign board overlay)
    ├──FK──► people.Person  (coordinator)
    ├──reverse FK──► response.Deployment  (field operations)
    ├──reverse FK──► response.EvacuationRoute
    └──reverse FK──► EmergencyStatus
```

`MobilizationEvent.clean()` validates that `community` matches `source_report.community`.

```
EmergencyStatus
    ├──FK──► MobilizationEvent
    ├──FK──► socialhub.Community  (nullable — at least one of community/zone required)
    ├──FK──► locations.Zone  (nullable)
    ├──FK──► people.Person  (declared_by)
    ├──FK──► assembly.AssemblyProposal  (source_proposal — the vote that authorized this)
    ├──FK──► assets.Asset  (emergency_tax_asset)
    ├──FK──► assets.LedgerAccount  (emergency_tax_account)
    └──reverse FK──► EmergencyEquipmentAccess (equipment_accesses)
```

**Emergency declaration flow** — direct creation is blocked. Must go through assembly vote:

1. Coordinator posts to `emergency_status_propose` → creates `AssemblyProposal(type="emg_declare")`
2. Community votes via the `assembly` app
3. Once `proposal.status == "passed"`, coordinator clicks **Activate Emergency** → `EmergencyStatus` created with `source_proposal` set

`EmergencyEquipmentAccess` tracks an `inventory.RealWorldObject` granted hybrid access under an active emergency. It optionally points to a `response.Deployment` that is using the item. `is_hybrid=True` marks shared access — ownership stays with the community/zone.

---

## Cross-app dependencies

| App | Models used | Purpose |
|---|---|---|
| `people` | `Person` | Responders, coordinators, reviewers |
| `socialhub` | `Community` | Incident community, responder affiliations, `is_federal_tribe` eligibility gate |
| `locations` | `Zone` | Emergency zone targeting |
| `inventory` | `RealWorldObject` | Hybrid emergency equipment items |
| `assets` | `Asset`, `LedgerAccount` | Emergency tax denomination and collection account |
| `competence` | `SkillBadge` | Responder skill registry |
| `detections` | `Detection` | Report evidence |
| `kanban` | `Campaign` | Campaign board overlay on events |
| `events` | `ScheduledEvent` | Optional calendar link |
| `assembly` | `AssemblyProposal` | Emergency declaration authorization gate |
| **`response`** | `Deployment`, `EvacuationRoute` | FK targets for event-level field operations |

### Key coupling points

- **`socialhub.Community.is_federal_tribe`** and **`people.Person.is_federal_agent`**: These flags gate responder eligibility in `Responder.clean()`. Changes to them can silently break responder registration.
- **`assembly` is write-only from mobilization**: The mobilization app creates `AssemblyProposal` records and reads their `status`. It never modifies assembly data.
- **`response` app**: `mobilization` is upstream. `response.Deployment` FKs into `mobilization.MobilizationEvent`. Mobilization never imports from response except for FK strings (`"response.Deployment"`).

---

## Services (`services.py`)

All business logic lives here. Views never modify models directly.

| Function | What it does |
|---|---|
| `can_enact_report(person, report)` | `True` if person is community head or senior member |
| `create_report_from_detection(community, detection, submitted_by)` | Creates report, links detection as `primary`/`high` evidence |
| `add_detection_evidence(report, detection, ...)` | Adds or updates evidence link; triggers severity recalc |
| `update_report_severity_from_evidence(report)` | Weighted-average severity recalculation |
| `submit_report(report, submitted_by)` | `draft → submitted` |
| `review_report(report, reviewed_by)` | `submitted → reviewed` |
| `enact_report(report, enacted_by, create_event=False)` | `→ enacted`; optionally creates event |
| `reject_report(report, rejected_by, notes)` | `submitted/reviewed → rejected` |
| `create_event_from_report(report, coordinator, ...)` | Creates `MobilizationEvent` from enacted report |
| `create_deployment(event, community, coordinator, ...)` | Creates `response.Deployment`; validates community match |
| `assign_responder_to_deployment(deployment, responder, ...)` | Creates `response.DeploymentAssignment`; raises if duplicate |
| `activate_deployment_assignment(assignment)` | `→ active`; responder `→ responding` |
| `release_responder_from_deployment(assignment)` | `→ released`; responder `→ available` if no other active |
| `create_intervention(deployment, **kwargs)` | Creates `response.Intervention` |
| `complete_intervention(intervention, outcome_notes)` | `→ done` |
| `complete_deployment(deployment, force_complete=False)` | `→ completed`; blocks on required interventions; cascades cleanup |

---

## Views and URLs (`/mobilization/`)

All views require `@login_required`. Mutation views require `@require_POST`.

| URL | View | Description |
|---|---|---|
| `/` | `overview` | Dashboard — responder counts, report pipeline, active events/deployments |
| `/responders/` | `responder_list` | Filterable list |
| `/responders/recruit/` | `responder_recruit` | Eligible persons not yet registered |
| `/responders/call-in/` | `responder_callin` | POST: creates Responder |
| `/responders/<pk>/` | `responder_detail` | Profile, skills, deployment history, badges |
| `/reports/` | `report_list` | Filterable by status and severity |
| `/reports/new/` | `report_create` | Create report |
| `/reports/<pk>/` | `report_detail` | Evidence list, enact/reject controls |
| `/reports/<pk>/submit/` | `report_submit` | POST: draft → submitted |
| `/reports/<pk>/review/` | `report_review` | POST: submitted → reviewed |
| `/reports/<pk>/enact/` | `report_enact` | POST: → enacted; optionally creates event |
| `/reports/<pk>/reject/` | `report_reject` | POST: → rejected |
| `/events/` | `event_list` | Filterable by status |
| `/events/<pk>/` | `event_detail` | Deployments, evac routes, escalation graph, mission timeline, emergency panel |
| `/events/<pk>/deployment/new/` | `deployment_create` | Create deployment; redirects to `response:deployment_detail` |
| `/events/<pk>/map-data/` | `event_map_data` | GeoJSON for overview map |
| `/events/<pk>/evac-routes/add/` | `evac_route_add` | POST: add evacuation route |
| `/events/<pk>/evac-routes/<pk>/status/` | `evac_route_status` | POST: update route status |
| `/events/<pk>/emergency/propose/` | `emergency_status_propose` | POST: create AssemblyProposal |
| `/events/<pk>/emergency/proposal/<pk>/activate/` | `emergency_proposal_activate` | POST: create EmergencyStatus from passed proposal |
| `/events/<pk>/emergency/<pk>/lift/` | `emergency_status_lift` | POST: lift emergency |
| `/events/<pk>/emergency/<pk>/equipment/` | `emergency_equipment_authorize` | POST: authorize hybrid equipment |

---

## Seeding

```bash
python manage.py ingress_mobilization
```

Seeds: `IncidentType` (8), `AchievementBadge` (8), `SkillBadge` group `mobilization` (7), up to 10 `Responder` records, then 5 full scenarios with reports, events, emergency statuses, and all deployment/response data (via `response` models). If no eligible persons exist, up to 6 are marked `is_federal_agent=True`.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assembly` — EmergencyStatus.source_proposal — declaration requires a passed AssemblyProposal
- `assets` — Resource requests reference Asset
- `competence` — Responder skill requirements checked against SkillBadge
- `inventory` — Equipment requests linked to RealWorldObject
- `kanban` — Emergency tasks created as Kanban Mission/Task
- `locations` — Incident location is an Address FK
- `people` — EmergencyContact and responder Person FKs
- `response` — Deployment and DeploymentAssignment live in response app
- `socialhub` — EmergencyStatus is community-scoped────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.ocr
========================================================================

# toto.ocr

*(Studio only — requires BUILD_STUDIO=1)*

Document OCR pipeline. Images uploaded to a vault bucket are processed through a configurable transform chain to extract text line by line.

## Purpose

An operator creates an `OcrProject` backed by a vault `Bucket`, then uploads document images. Each `OcrImage` is queued for processing through an ordered chain of `ImageTransform` steps (resize, grayscale, threshold…) before the OCR engine extracts `OcrLine` records with bounding boxes and confidence scores. Custom transforms delegate to `workflows.LambdaFunction`. Extracted text feeds downstream into palimpsest pages or memo decks.

## Models

- `OcrProject` — a named OCR workspace. Fields: `name`, `slug`, `bucket` (FK to `vault.Bucket`), `owner` (FK to `auth.User`), `allowed_users` (M2M to `auth.User`), `language`, `is_active`, `created_at`.

- `OcrImage` — one image in a project queued for OCR. Fields: `project` FK, `vault_file` (FK to `vault.VaultFile`), `status` (`pending / processing / done / failed`), `page_number`, `processed_at`, `error_message`.

- `OcrLine` — a single text line extracted from an image. Fields: `image` FK, `line_number`, `text`, `confidence` (float 0–1), `bounding_box` (JSON — `{x, y, w, h}` as fractions of image size).

- `ImageTransform` — a processing step applied to images before OCR. Fields: `project` FK, `name`, `order`, `transform_type` (`resize / grayscale / threshold / denoise / deskew / crop`), `lambda_function` (FK to `workflows.LambdaFunction`, nullable — custom transform), `is_active`.

- `ImageTransformParam` — a named parameter for a transform. Fields: `transform` FK, `key`, `value` (string).

## Key coupling

- `vault.VaultFile` / `vault.Bucket` — images are stored in vault.
- `workflows.LambdaFunction` — custom transform steps delegate to workflow lambdas.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `vault` — OcrImage.vault_file FK to VaultFile; project backed by vault.Bucket
- `workflows` — ImageTransform.lambda_function FK to workflows.LambdaFunction────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.palimpsest
========================================================================

# toto.palimpsest

Collaborative long-form writing. Multi-author documents composed of ordered sections. Authors are inferred from section authorship — no single owner.

## Purpose

Any member can create a `Page` and add `Section` blocks authored by different people. There is no single page owner — authorship is distributed across sections. Pages accumulate `word_count` and `reading_time_minutes` properties from their sections automatically. This makes palimpsest suitable for community manifests, field notes, and collaborative essays where multiple voices contribute to a single document.

## Models

- `Tag` — extends `verbena.AbstractTag`. Tagging for palimpsest pages.

- `Page` — extends `verbena.AbstractPage`. A collaborative document. Tags M2M to `Tag`. Key properties:
  - `authors()` — returns `People.Person` queryset: all persons who authored at least one section
  - `word_count` — summed across all sections + description
  - `reading_time_minutes` — `max(1, word_count / 220)`
  - `excerpt` — first section's content if no description

- `Section` — extends `verbena.AbstractSection`. One authored block within a page. Fields: `page` (FK to `Page`), `title`, `order`, `content` (rich text), `author` (FK to `people.Person`).

## Key coupling

- `verbena.AbstractPage` / `AbstractSection` — concrete implementations.
- Authors are `people.Person` records; no community scoping (palimpsest pages are platform-wide by default).

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — Section.author FK to Person
- `verbena` — Page extends AbstractPage; Section extends AbstractSection────────────────────────────────────────────────────────────────────────



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

`is_federal_agent` and community membership (via `communities` M2M) are read by `mobilization.Responder.clean()` to enforce eligibility.

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
  APP: toto.quizzes
========================================================================

# toto.quizzes

Personality and assessment quiz engine. Quizzes produce trait scores rather than right/wrong grades, making them suitable for self-assessment, role matching, and onboarding surveys.

## Models

- `Quiz` — a named quiz. Fields: `title`, `description`, `community` (FK), `is_active`, `is_public`, `created_by` (FK to `people.Person`).

- `QuizQuestion` — a question within a quiz. Fields: `quiz`, `text`, `order`, `is_required`.

- `QuizAnswer` — one answer option for a question. Fields: `question`, `text`, `order`.

- `QuizAnswerTrait` — links an answer to a trait with a score weight. Fields: `answer`, `trait` (FK to `QuizTrait`), `weight` (float). Multiple traits can be affected by a single answer.

- `QuizTrait` — a named dimension being measured. Fields: `quiz`, `name`, `slug`, `description`. Example: `leadership`, `technical_aptitude`, `empathy`.

- `QuizAttempt` — one person's completed quiz attempt. Fields: `quiz`, `person` (FK to `people.Person`), `started_at`, `completed_at`, `is_complete`, `result_metadata` (JSON — computed trait scores).

- `QuizAttemptAnswer` — the answer selected in one attempt. Fields: `attempt`, `question`, `selected_answer`.

## Key coupling

- Standalone; no hard FK dependencies on other domain apps.
- Results (`result_metadata`) are stored as JSON on the attempt and can be read by `academy` enrollment flows or community onboarding.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `people` — QuizAttempt.participant FK to Person────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.ravioli
========================================================================

# toto.ravioli

*(Studio only — requires BUILD_STUDIO=1)*

Sole boundary to Neo4j. All apps that need graph storage emit `GraphChangeEvent` records; a Celery worker drains them and applies upserts/deletes to Neo4j. Apps never call Neo4j directly.

## Purpose

The graph layer lets toto answer questions that relational queries can't handle efficiently: "find all communities connected within 3 hops", "shortest path between two members", "what instruments does this account participate in". Apps emit `GraphChangeEvent` records through Django signals — ravioli drains them in batches to Neo4j. Stored `CypherQuery` records let admins run graph queries from the dashboard. `GraphProjectionPlan` manages full-resync operations when the graph drifts from Postgres.

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

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.response
========================================================================

# Response App

Field operations layer for emergency response. Handles everything that happens after a `mobilization.MobilizationEvent` is created: deployments, responder assignments, interventions, routes, and equipment. The upstream command pipeline lives in the `mobilization` app.

---

## How it works

```
mobilization.MobilizationEvent  ◄── FK anchor (owned by mobilization)
    │
    └──► Deployment  (planned → active → paused → completed / cancelled)
              │
              ├──► DeploymentAssignment  ──► mobilization.Responder
              │         (assigned → confirmed → active → released / completed / no_show)
              │
              ├──► Intervention  ──── mitigates ──► detections.Detection
              │         (todo → assigned → in_progress → done / cancelled)
              │
              ├──► DeploymentRoute  ──► locations.Route
              ├──► DeploymentEquipment  ──► inventory.RealWorldObject
              └──► EmergencyEquipmentAccess  (from mobilization.EmergencyStatus)

mobilization.MobilizationEvent
    └──► EvacuationRoute  ──► locations.Route
              (planned / active / blocked / cleared)
```

---

## Internal model relationships

### Reference model

`InterventionType` — admin-managed lookup used by `Intervention.intervention_type`. Types are seeded by `ingress_mobilization` (13 records). The only way to add/edit types is the Django admin (no UI form).

### Deployment subtree

```
Deployment
    ├──FK──► mobilization.MobilizationEvent  (event)
    ├──FK──► socialhub.Community  (must match event.community)
    ├──FK──► kanban.Mission  (optional mission board link)
    ├──FK──► people.Person  (coordinator)
    │
    ├──reverse FK──► DeploymentAssignment
    │                    ├──FK──► mobilization.Responder
    │                    └──FK──► people.Person  (assigned_by)
    │
    ├──reverse FK──► Intervention
    │                    ├──FK──► InterventionType
    │                    ├──FK──► detections.Detection  (threat being mitigated)
    │                    ├──FK──► mobilization.Responder  (assigned_to)
    │                    ├──FK──► people.Person  (reported_by, reviewer)
    │                    ├──FK──► assets.Asset  (cost_asset, reward_asset)
    │                    ├──FK──► assets.Currency  (reward_currency)
    │                    └──FK──► kanban.Task  (optional task board link)
    │
    ├──reverse FK──► DeploymentRoute ──FK──► locations.Route
    └──reverse FK──► DeploymentEquipment ──FK──► inventory.RealWorldObject
```

**Integrity constraint** — `Deployment.clean()` validates `community == event.community`. This is also enforced by `mobilization.services.create_deployment()`.

**Completion guard** — `complete_deployment()` raises `ValidationError` if any `Intervention` with `is_required=True` is not in `done` or `cancelled` state. Pass `force_complete=True` to bypass.

### DeploymentAssignment

Links a `mobilization.Responder` to a `Deployment` with a role and lifecycle status.

- Roles: lead / deputy / driver / medic / logistics / communicator / responder / volunteer
- `unique_together = (deployment, responder)` — one assignment per responder per deployment
- `activate_deployment_assignment()` sets `responder.current_status = "responding"`
- `release_responder_from_deployment()` sets `responder.current_status = "available"` if no other active assignment remains

### Intervention

A discrete task within a deployment. Can be linked to a `detections.Detection` it is intended to mitigate, creating a traceable chain from detected threat → field action.

- `is_required = True` blocks `complete_deployment()` until resolved
- Cost fields: `estimated_cost`, `actual_cost`, `cost_asset`, `cost_center` — who bears the cost
- Reward fields: `reward_amount`, `reward_asset`, `reward_currency` — compensation for the assigned responder
- Review fields: `reviewer` (person), `reviewed_at`, `effect_description` — post-action assessment

### Routes

- **`EvacuationRoute`** — a `locations.Route` designated at the event level. Managed from the mobilization `event_detail` page (evac route views stay in `mobilization`). Statuses: planned / active / blocked / cleared.
- **`DeploymentRoute`** — a route assigned to a specific deployment. Types: primary / alternate / supply / retreat / other.

---

## Cross-app dependencies

| App | Models used | Purpose |
|---|---|---|
| **`mobilization`** | `MobilizationEvent`, `Responder` | FK anchor for deployments/evac routes; responder registry |
| `people` | `Person` | Coordinators, assignees, reviewers |
| `socialhub` | `Community` | Deployment community (must match event community) |
| `locations` | `Route` | Evacuation and deployment routes |
| `inventory` | `RealWorldObject` | Deployment equipment |
| `assets` | `Asset`, `Currency` | Intervention cost/reward denomination |
| `detections` | `Detection` | Intervention mitigation target; report evidence (read-only) |
| `kanban` | `Mission`, `Task` | Optional mission board overlay on deployments/interventions |

### Key coupling points

- **`mobilization.Responder` status** is mutated by response-side service calls. `response` owns the transitions (`responding` / `available`) but the `Responder` model lives in `mobilization`.
- **`detections.Detection`** is linked to an `Intervention` via `Intervention.detection`. The deployment detail page surfaces evidence detections from the event's source report to suggest which detections need a mitigation intervention.
- **`mobilization.EmergencyEquipmentAccess.deployment`** FKs into `response.Deployment` — when an emergency grants hybrid equipment access, it is tied to a specific deployment from this app.

---

## Services (in `mobilization/services.py`)

Response operations are intentionally orchestrated from `mobilization.services` to keep business logic centralized. The service functions import from `response.models` as needed.

| Function | What it does |
|---|---|
| `create_deployment(event, community, coordinator, ...)` | Creates `Deployment`; validates community match |
| `assign_responder_to_deployment(deployment, responder, ...)` | Creates `DeploymentAssignment`; raises on duplicate |
| `activate_deployment_assignment(assignment)` | `→ active`; responder `→ responding` |
| `release_responder_from_deployment(assignment)` | `→ released`; responder `→ available` if no other active |
| `create_intervention(deployment, **kwargs)` | Creates `Intervention` |
| `complete_intervention(intervention, outcome_notes)` | `→ done` |
| `complete_deployment(deployment, force_complete=False)` | `→ completed`; blocks on required interventions; cascades assignment/responder cleanup |

---

## Views and URLs (`/response/`)

All views require `@login_required`. Mutation views require `@require_POST`.

| URL | View | Description |
|---|---|---|
| `/deployments/` | `deployment_list` | Filterable by status |
| `/deployments/<pk>/` | `deployment_detail` | Assignments, interventions with linked detections, routes, equipment |
| `/deployments/<pk>/map-data/` | `deployment_map_data` | GeoJSON for deployment map |
| `/deployments/<pk>/assign/` | `assignment_create` | POST: assign a responder |
| `/deployments/<pk>/activate/<assignment_pk>/` | `assignment_activate` | POST: assignment → active |
| `/deployments/<pk>/release/<assignment_pk>/` | `assignment_release` | POST: release responder |
| `/deployments/<pk>/complete/` | `deployment_complete` | POST: mark complete |
| `/deployments/<pk>/intervention/new/` | `intervention_create` | Create intervention; shows event evidence detections |
| `/deployments/<pk>/routes/add/` | `deployment_route_add` | POST: add route |
| `/deployments/<pk>/equipment/add/` | `deployment_equipment_add` | POST: add inventory item |
| `/interventions/<pk>/done/` | `intervention_complete` | POST: → done |
| `/interventions/<pk>/review/` | `intervention_review` | POST: set reviewer, effect description, reward |

> **Note:** Deployment creation (`/mobilization/events/<pk>/deployment/new/`) and evacuation route management stay in the `mobilization` app since they are event-level actions. After creation, navigation redirects to `response:deployment_detail`.

---

## Responder status flow

```
off_duty ──► available ──► standby ──► responding
    ▲             ▲                        │
    │             └────────────────────────┘
    └─── unavailable (manual)
```

Transitions happen automatically through service calls:
- `activate_deployment_assignment()` → `responding`
- `release_responder_from_deployment()` (no more active assignments) → `available`
- `complete_deployment()` — cascades release for all active assignments

---

## Seeding

Seeded by `python manage.py ingress_mobilization` alongside mobilization data. Requires inventory ingress to have run first for `DeploymentEquipment` and `EmergencyEquipmentAccess` records to be created.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — Intervention cost/reward denominated in Asset
- `inventory` — DeploymentEquipment links to RealWorldObject
- `kanban` — Deployment may link to Kanban Mission
- `locations` — EvacuationRoute and DeploymentRoute reference Address
- `mobilization` — Deployment FK to mobilization.EmergencyEvent
- `people` — DeploymentAssignment assignee is a Person
- `socialhub` — Deployment.community scoping────────────────────────────────────────────────────────────────────────



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
  - `is_federal_tribe` — bool; marks this community as part of the federal tier. Members of federal tribes are eligible as responders.
  - `head` — FK to `people.Person` (community leader)
  - `ledger_account` — FK to `assets.LedgerAccount` (community treasury)
  - `theme` — FK to `core.Theme`
  - `tax_rate`, `tax_asset` — optional default transaction tax settings

- `CommunityNewsTopic` — extends `AbstractTag`. Tag/category for news posts.

- `CommunityNewsPost` — extends `AbstractSection`. A rich-text news article inside a community.
  - `community` — FK
  - `author` — FK to `people.Person`
  - `is_published`, `published_at`

- `MembershipApplication` — a person's request to join a community.
  - `applicant` — FK to `people.Person`
  - `community` — FK
  - `status` — `pending / approved / rejected`
  - `reviewed_by` — FK to `people.Person`

- `ReferenceRequest` — a request from one person to another asking for a reference letter (used in membership applications).
  - `requester`, `target` — FKs to `people.Person`
  - `community` — FK
  - `status` — `pending / provided / declined`

## Key coupling

- `Community` is referenced by nearly every domain model (kanban projects, bazaar shops, assembly proposals, mobilization events, emergency statuses, deployments).
- `Community.is_federal_tribe` gates `mobilization.Responder` eligibility.
- `Community.ledger_account` is used as the default creditor for community fees and taxes.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `api` — EmailService FK for community notification emails
- `locations` — Community headquarters Address FK
- `people` — CommunityNewsPost author; MembershipApplication applicant────────────────────────────────────────────────────────────────────────



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

- `manifest.ManifestBundle` — a dataclass exported by an SSO consumer app (e.g. `regis`). Declares what OIDC client it needs: `client_id`, `client_type`, `redirect_uris`, `scopes`, `trusted`.
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

toto acts as its own identity provider. External apps (e.g. `regis`) and internal services register as OIDC clients. When a user logs into a client app, they are redirected to toto's `/sso/authorize/` endpoint, authenticate, and receive a JWT ID token signed with the active `SSOSigningKey`. The private signing key never leaves gervazy — `services.get_signing_private_key_pem()` unlocks it at token-issue time using `SSO_VAULT_PASSWORD`. The JWKS endpoint (`/sso/jwks/`) lets relying parties verify token signatures.

## Models

- `SSOClient` — a registered OIDC client application. Fields: `client_id` (unique), `client_secret` (hashed), `client_type` (`confidential / public`), `name`, `redirect_uris` (text, one per line), `scopes` (space-separated), `is_active`, `is_trusted` (trusted clients skip the consent screen), `metadata`.

- `SSOSubject` — links a Django `auth.User` to an OIDC `sub` claim (stable UUID per user per client). Fields: `user` FK, `client` FK, `sub` (UUID). Unique on `(user, client)`.

- `SSOAuthorizationCode` — a short-lived authorization code issued during the authorization code flow. Fields: `client`, `user`, `code` (unique), `redirect_uri`, `scope`, `nonce`, `code_challenge` / `code_challenge_method` (PKCE), `expires_at`, `is_used`.

- `SSOSigningKey` — an RSA key pair used to sign JWTs. Fields: `key_id` (UUID `kid` claim), `algorithm` (default `RS256`), `public_key_pem` (plaintext), `encrypted_key` (FK to `gervazy.EncryptedPrivateKey`), `is_active`, `created_at`. Only one key is active at a time.

- `SSOAccessToken` — an issued access token (stored for introspection). Fields: `client`, `user`, `token` (hashed), `scope`, `expires_at`, `is_revoked`, `issued_at`.

- `SSORelyingParty` — extends `SSOClient`. An explicitly provisioned relying party (e.g. a `regis` deployment). Adds: `display_name`, `description`, `connection_bundle_hash` (SHA-256 of the imported connection bundle).

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

# toto.steven

*(Studio only — requires BUILD_STUDIO=1)*

AI agent management. Defines named agent profiles with LLM configuration and tool sets. Runs are tracked per-agent with full input/output and token usage.

## Models

- `AgentConnector` — extends `api.ApiConnector`. LLM-specific outbound connector. Adds: `model_name` (e.g. `claude-sonnet-4-6`), `system_prompt`, `temperature`, `max_tokens`, `provider` (`anthropic / openai / mistral / custom`).

- `AgentProfile` — a named autonomous agent. Fields: `name`, `slug`, `user` (OneToOne FK to `auth.User` — the agent's identity for actions it takes), `connector` (FK to `AgentConnector`), `description`, `is_active`, `community` (FK, nullable), `metadata`.

- `AgentTool` — a tool available to an agent. Fields: `agent` (FK to `AgentProfile`), `tool_type` (`web_search / code_exec / file_read / api_call / workflow_trigger`), `config` (JSON), `is_enabled`.

- `Conversation` — a thread of messages for a user's session with an agent. Fields: `agent` FK, `user` (FK to `auth.User`), `title`, `started_at`, `last_message_at`, `is_archived`.

- `ChatMessage` — one message in a conversation. Fields: `conversation` FK, `role` (`user / assistant / system / tool`), `content` (text), `tool_calls` (JSON), `tool_results` (JSON), `created_at`, `tokens_used`.

- `AgentRun` — a single programmatic agent invocation (outside a conversation). Fields: `agent` FK, `triggered_by` (FK to `people.Person`, nullable), `workflow_run` (FK to `workflows.WorkflowRun`, nullable), `status` (`pending / running / success / failed`), `input_data` (JSON), `output_data` (JSON), `error_message`, `tokens_input`, `tokens_output`, `started_at`, `finished_at`.

## Key coupling

- `api.ApiConnector` — `AgentConnector` inherits all connector fields; secrets live in `gervazy`.
- `workflows.WorkflowRun` — agents can be invoked as workflow nodes (`agent_call`).

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `api` — AgentConnector subclasses ApiConnector for LLM provider config────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.texlab
========================================================================

# toto.texlab

*(Studio only — requires BUILD_STUDIO=1)*

LaTeX compilation service over WebSockets. Workspaces hold `.tex` and supporting files; compile runs invoke a LaTeX engine and return PDF or image output.

## Models

- `LatexWorkspace` — a named LaTeX project. Fields: `name`, `slug`, `bucket` (FK to `vault.Bucket` — file storage for this workspace), `owner` (FK to `people.Person`), `is_public`, `created_at`.

- `LatexFile` — a file within a workspace. Fields: `workspace` FK, `filename`, `vault_file` (FK to `vault.VaultFile`), `is_main` (the entrypoint `.tex` file), `updated_at`.

- `CompileRun` — a single compilation attempt. Fields: `workspace` FK, `latex_file` (FK to the main file), `status` (`queued / running / success / failed`), `compiler` (`pdflatex / xelatex / lualatex`), `output_pdf` (FK to `vault.VaultFile`, nullable), `log_output` (text), `duration_ms`, `workflow_run` (FK to `workflows.WorkflowRun`, nullable — for scheduled compiles), `created_at`.

## How it works

- Compile requests are submitted via WebSocket or HTTP.
- The Channels consumer queues a `CompileRun`, streams log output back to the client in real time, and stores the output PDF in vault on success.

## Key coupling

- `vault.VaultFile` / `vault.Bucket` — all files stored via vault.
- `workflows.WorkflowRun` — compile runs can be triggered by the workflow engine.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `vault` — LatexFile and CompileRun output stored as VaultFile
- `workflows` — CompileRun can be triggered as a workflow node────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.travels
========================================================================

# toto.travels

*(Studio only — requires BUILD_STUDIO=1)*

Route journeys and visit history. Records group trips with geographic waypoints and individual visit reviews.

## Models

- `Travel` — extends `DomainEntity`. A named journey. Fields: `participants` (M2M to `people.Person`), `route` (FK to `locations.Route`, nullable), `community` (FK, nullable), `starts_at`, `ends_at`, `description`, `status` (`planned / ongoing / completed / cancelled`).

- `Visit` — extends `DomainEntity`. One person's visit to a location. Fields: `participant` (FK to `people.Person`), `location` (FK to `locations.Address`), `travel` (FK, nullable), `visited_at`, `duration_minutes`, `notes`, `rating` (1–5), `is_public`.

## Key coupling

- `locations.Route` / `locations.Address` — geographic anchors for journeys and visits.
- `people.Person` — participants and visitors.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `locations` — Travel waypoints and Visit.location are Address FKs
- `people` — Travel participants and Visit.person FK────────────────────────────────────────────────────────────────────────



========================================================================
  APP: toto.tribunal
========================================================================

# toto.tribunal

Dispute resolution and case management. Members open cases against each other or against commercial transactions. Cases proceed through a jury session to a binding ruling.

## Purpose

A member files a `TribunalCase` against another member, a vendor, or a disputed order. Parties are registered, claims are specified (monetary or performance), and evidence is uploaded. The case moves to a `JurySession` where selected community members vote. A `TribunalRuling` closes the case — monetary awards create `assets.Obligation` records against the losing party's ledger account, making enforcement traceable.

## Models

- `TribunalCase` — extends `DomainEntity`. The root record. Fields: `community` (FK), `case_number` (auto-generated unique), `opener` (FK to `people.Person`), `assignee` (FK to `people.Person` — judge or mediator), `reason` (`breach_of_contract / fraud / damage / harassment / other`), `status` (`open / in_review / in_jury / ruled / closed / dismissed`), `linked_object` (FK to `inventory.RealWorldObject`, nullable), `linked_order` (FK to `bazaar.Order`, nullable).

- `TribunalParty` — extends `DomainEntity`. A person's role in a case. Fields: `case`, `person`, `role` (`complainant / respondent / witness / representative`).

- `TribunalClaim` — extends `DomainEntity`. A specific assertion within a case. Fields: `case`, `claimant` (FK to `people.Person`), `claim_type` (`monetary / specific_performance / declaratory / injunctive`), `description`, `amount_base_units` (nullable), `asset` (nullable FK to `assets.Asset`), `status` (`pending / upheld / dismissed`).

- `TribunalEvidence` — extends `DomainEntity`. An item of evidence. Fields: `case`, `submitted_by` (FK to `people.Person`), `evidence_type` (`document / photo / testimony / transaction_record / other`), `vault_file` (FK to `vault.VaultFile`, nullable), `description`, `submitted_at`.

- `JurySession` — extends `DomainEntity`. The voting phase of a case. Fields: `case` (OneToOne), `jurors` (M2M to `people.Person`), `started_at`, `ends_at`, `quorum`, `verdict` (`guilty / not_guilty / hung`), `is_closed`.

- `JuryVote` — extends `DomainEntity`. A single juror's vote. Fields: `session`, `juror` (FK to `people.Person`), `vote` (`guilty / not_guilty / abstain`), `notes`. Unique on `(session, juror)`.

- `TribunalRuling` — extends `DomainEntity`. The final ruling issued after jury verdict or direct settlement. Fields: `case` (OneToOne), `issued_by` (FK to `people.Person`), `ruling_type` (`monetary_award / specific_performance / dismissal / settlement`), `summary`, `amount_base_units`, `asset`, `enforcement_deadline`.

## Key coupling

- `bazaar.Order` — commercial disputes link to the order in question.
- `inventory.RealWorldObject` — property disputes link to the item.
- `assets.LedgerAccount` — monetary awards create `assets.Obligation` records for enforcement.
- `vault.VaultFile` — documentary evidence is stored in vault.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

- `assets` — TribunalRuling compensation denominated in Asset
- `bazaar` — TribunalCase can reference a disputed bazaar Order
- `inventory` — TribunalClaim can reference a RealWorldObject
- `people` — TribunalParty and JuryVote.juror are Person records────────────────────────────────────────────────────────────────────────



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

User file storage. Organizes user-uploaded files into named buckets with optional access control via gateways.

## Models

- `Bucket` — a named file container. Fields: `owner` (FK to `auth.User`), `name`, `is_public`, `created_at`.
- `VaultFile` — a file stored in a bucket. Fields: `owner` FK, `bucket` FK (nullable), `original_filename`, `content_type`, `size`, `file` (Django `FileField`), `checksum`, `uploaded_at`, `is_deleted`, `deleted_at`. Soft-delete only.
- `FileGateway` — sharing gate on a bucket. Fields: `bucket` (OneToOne), `token` (UUID slug for URL access), `allowed_users` (M2M to `auth.User`), `is_public`, `expires_at`.

## Key coupling

- `library.LibraryItem.vault_file` — books, articles, audio, video reference their source file here.
- `ocr.OcrImage.vault_file` — OCR images are vault files.
- `texlab.LatexFile.vault_file` — LaTeX source files live in vault.
- `gervazy.EncryptedFile` is a separate encrypted-at-rest store; `vault` is for unencrypted / user-accessible files.

──────────────────────────────────────────────── DEPENDENCIES ──────────
## Dependencies

None — standalone app.────────────────────────────────────────────────────────────────────────



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

