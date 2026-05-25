# toto

**toto** is a Django-based platform for running tokenized community economies. It combines democratic governance, a ledger-backed asset system, financial instruments, a marketplace, project management, and learning infrastructure into a single coherent system. Communities on toto can issue assets, vote on rules, levy taxes, trade, run projects, learn, and settle disputes — all tied together through a shared identity and cryptographic foundation.

---

## The Idea

The central claim of toto is that a community can operate like a mini-state: it has members, money, rules, a parliament, a court, and an economy. Everything economically meaningful — physical objects, projects, claims, obligations — can be tokenized and tracked on a double-entry ledger. Governance decisions are immutable and hash-chained. Secrets are encrypted at rest with a per-user vault. The graph of all relationships between objects lives in Neo4j, synchronized from Postgres through a single boundary app.

The system is designed for communities that want to self-govern — setting their own transaction fees, poll taxes, and rules through on-chain votes — while still depending on a shared infrastructure: shared identity (OIDC), shared assets, shared marketplace.

---

## System Traits

- **Django monorepo.** Three Django projects coexist in one repo: `toto/` (the main platform), `portal/` (management portal), and `regis/` (geophysical/economic simulation). Apps are installed from the `toto.toto` package namespace.
- **Double-entry ledger.** Every balance change posts to `LedgerEntry`. Entries are immutable after creation. Transactions become immutable once posted. A hash-chain (`LedgerHash`) ties every transaction to the previous one — tamper evidence at the DB level.
- **Lapis smart contracts.** A custom YAML-based contract VM lives in `assets.Contract`. Financial instruments generate Lapis programs; the VM validates state transitions. Contracts can be authored visually in the `contracts` graph editor.
- **Hierarchical encryption.** `gervazy` implements a three-tier key hierarchy: password → Argon2id KDF → UKEK → AES-256-GCM wrapped VMK → wrapped DEK → encrypted objects (secrets, files, private keys). Nothing sensitive is stored in plaintext.
- **OIDC provider.** `sso_master` implements a full OpenID Connect server. Any toto app or external service can use toto as its identity provider. Signing keys are RSA and stored encrypted in gervazy.
- **Graph layer.** `ravioli` is the sole boundary to Neo4j. Every model that should appear in the graph emits `GraphChangeEvent` records; a Celery worker drains them and applies upserts/deletes to Neo4j. Apps never call Neo4j directly.
- **Real-time over WebSockets.** Django Channels powers live chat (enigma), whiteboard (sketch), LaTeX compilation (texlab), and compute kernels (mandragora).
- **Community-governed economy.** Each community runs an `Assembly` where members vote on proposals. Passed proposals are recorded as immutable `AssemblyDecision` records and may enact `CommunityRule`, `CommunityTransactionFee`, or `PollTax` objects. An optional `CommunitySenate` can veto proposals within a window.
- **Plugin system.** `core.plugin_autodiscover` lets apps register fee and profile plugins without hard coupling. Bazaar fees are computed through a plugin chain.
- **Celery + Redis.** Background tasks handle ledger operations, graph sync, subscription billing, instrument lifecycle, workflows, and periodic tax collection.
- **Modular deployment.** `deploy.py` reads YAML config files to build settings for each deployment target.

---

## Deployment Tiers: Base and Studio

The platform ships in two distinct modes controlled by the `BUILD_STUDIO` environment variable. This is the single most important architectural division in the codebase.

### Base (BUILD_STUDIO=0) — `portal_mini`

Runs under **gunicorn** (WSGI). No WebSockets, no Neo4j, no Celery workers, no compute kernel. Requires only Postgres (PostGIS) and optionally Redis for caching. This is the lighter deployment: it carries the full identity, financial, governance, marketplace, learning, and project-management stack without the real-time and compute layers.

**Infrastructure required:** Postgres/PostGIS · Redis (cache only) · Nginx · optional: Redis (Celery broker for background tasks even in base mode — beat schedule for daily allowances and subscription billing still runs)

### Studio (BUILD_STUDIO=1) — `portal_max`

Adds on top of base. Runs under **uvicorn/daphne** (ASGI). Enables Django Channels (WebSocket layer backed by Redis channel layer), Neo4j graph database (managed exclusively through ravioli), the ZMQ compute kernel server (mandragora), and all real-time/compute apps.

**Additional infrastructure:** Redis channel layer · Neo4j 5 + APOC plugin · Compute kernel server (ZMQ, port 5555) · Prometheus + Grafana + Loki + Promtail (in max config)

---

## Apps

Apps are listed in the order they appear in `INSTALLED_APPS`. Third-party libraries are described alongside the toto apps that depend on them.

---

### Framework and Third-Party (base)

**`daphne`** — ASGI server from the Django Channels project. Installed in base too because it registers the `ASGI_APPLICATION` Django setting handler; gunicorn is used in base mode and daphne/uvicorn in studio mode.

**`django_prometheus`** — Exposes a `/metrics` endpoint in Prometheus exposition format. Wraps each request with before/after middleware that records request counts, latencies, and DB query stats. Used as the health-check target in the WSGI healthcheck (`curl /metrics`).

**`django.contrib.admin`** — Standard Django admin. All toto models register custom `ModelAdmin` subclasses through per-app `admin.py` files, most extending `toto.core.base_admin.TotoBaseAdmin` which adds read-only protection, inline helpers, and soft-delete awareness.

**`django.contrib.auth` / `contenttypes` / `sessions` / `messages` / `staticfiles`** — Django's standard foundation.

**`django.contrib.gis`** — Provides GIS field types (`PointField`, `PolygonField`, `MultiPolygonField`, `MultiLineStringField`) backed by PostGIS. Required by `locations` and `regis.geophysics`. In development with SQLite, falls back to SpatiaLite via `mod_spatialite`.

**`corsheaders`** — Django CORS headers middleware. Needed when the REST API or SSO endpoints are called from a different origin (e.g. a mobile client or a remote relying party).

**`django_jsonform`** — Renders JSON/JSONSchema fields as structured HTML form widgets. Used in `core` (`Theme.header`, `Theme.footer` fields) and elsewhere to avoid raw JSON textarea editing.

**`django_json_widget`** — JSON editor widget for the Django admin. Adds a syntax-highlighted, collapsible JSON editor for any `JSONField` in admin views.

**`rest_framework`** — Django REST Framework. Used by `bazaar`, `enigma`, `ravioli`, and `workflows` for serializer-backed API views. Not the primary UI layer (most views are server-rendered), but present for machine-readable endpoints.

**`colorfield`** — `ColorField` model field with a color-picker widget. Used exclusively by `core.ColorMix` to store the platform theme's hex color palette.

**`reversion`** — Model version history. Records `create`/`update`/`delete` events per save, enabling audit trails and rollback for sensitive records.

**`markdownx`** — Markdown editor with live preview, used in forms where lightweight markup is preferred over full Trix rich text.

**`trix_editor`** — Django field and widget for Basecamp's Trix rich-text editor. Powers `verbena.AbstractSection.content`, which is the content body used across `palimpsest`, `kanban`, `socialhub`, `academy`, and `memo`.

---

### Foundation (base)

**`toto.core`** — Platform configuration root and shared utilities.
- `Platform` — one row per deployment: domain, site name, rate limit window/max-requests, theme FK, federation FK, logo, API owner.
- `Federation` — a named group that communities belong to (e.g. a network of autonomous organizations).
- `Theme` / `ColorMix` / `Font` — composable UI theming: ColorMix holds 22+ named color slots for light and dark mode; Theme binds a ColorMix + Font + Tailwind class snippets for header/footer; Platform points to a Theme.
- `DomainEntity` — abstract base model used by most domain objects. Adds nothing at the DB level but marks the lineage.
- `PlatformMiddleware` — injects the active `Platform` into every request so templates can access the theme and site name without a DB query per view.
- `auth_cooldown` — rate-limits login attempts per IP using the cache backend.
- `plugin_autodiscover` / `plugin.py` — app-level plugin registry. Apps declare plugins (fee plugins, profile plugins) in a well-known submodule; `core` discovers and registers them at startup.
- `ingress.py` — data ingestion helpers used at startup to seed the database from fixture-like Python objects when `FULL_INGRESS=1`.
- `batch.py` — bulk-write helpers used during ingress and migrations.
- `connectors.py` / `domain.py` — base connector patterns for inter-app service calls.

**`toto.api`** — External service connector registry.
- `ApiConnector` — stores a base URL and sanitized JSON credentials for an external HTTP service. The `_reject_secret_like_json` validator blocks storing raw secrets in the credential blob.
- `EmailService` — SMTP configuration (host, port, TLS, username, hashed password) for outbound email. Communities point to an EmailService for member notifications.

**`toto.backup`** — Backup management. Records backup job runs, stores metadata about backup archives, and exposes admin views for triggering and monitoring backups.

---

### Security (base)

**`toto.gervazy`** — Encryption-at-rest. Implements a three-tier AES-256-GCM key hierarchy:

| Layer | Model | What it holds |
|-------|-------|---------------|
| 1 | `UserStrongbox` (`UserVault`) | Argon2id KDF params (salt, memory cost, iterations, lanes). No password stored. |
| 2 | `VaultMasterKey` | VMK ciphertext encrypted by the password-derived UKEK. One active VMK per strongbox. |
| 2b | `WrappedDataKey` | DEK ciphertext wrapped by the VMK. One DEK per class of protected object. |
| 3 | `EncryptedSecret` | AES-256-GCM ciphertext of a secret value (API token, SMTP password, wallet PIN, OAuth secret). |
| 3 | `EncryptedFile` + `EncryptedFileChunk` | Chunked AES-256-GCM ciphertext of a binary file. Original filename is also encrypted. |
| 3 | `EncryptedPrivateKey` | AES-256-GCM ciphertext of an RSA/Ed25519 private key. Public key is stored in plaintext. |

`CryptoAuditLog` is an append-only UUID-keyed record of every encrypt/decrypt/rotate operation. Its `save()` method hard-rejects any `reason` string that contains words like `password=`, `secret=`, or `private_key`.

`crypto.py` holds the pure-function encryption primitives (derive key, encrypt, decrypt, self-signed cert generation) that operate on raw bytes.

**`toto.vault`** — Media file registry. `VaultFile` tracks uploaded files (images, SVGs, PDFs) with type metadata and public URL resolution. Used by `memo` (SVG diagrams), `library` (document attachments), and anywhere else the system needs managed file references beyond Django's raw `FileField`.

---

### Identity (base)

**`toto.people`** — The primary identity object. `Person` is the human actor across all domains:
- One-to-one with `django.contrib.auth.User` (optional — persons can be pre-created without a user account).
- `communities` M2M to `socialhub.Community` (stored in the legacy `socialhub_person_communities` through-table).
- `patron` FK to self — mentor/mentee chains.
- `address` FK to `locations.Address`.
- `is_federal_agent` flag — exempts the person from all community poll taxes.
- `slug` auto-generated from `display_name`.

Nearly every other app links to `Person` rather than `User` directly. The FK from `Person` to `User` means that when an SSO login occurs and a user is created, the matching `Person` record becomes the platform identity.

**`toto.sso_core`** — OIDC shared manifest. Declares the supported scopes (`openid`, `email`, `profile`) and claim mappings used by both `sso_master` (provider) and `sso_client` (consumer).

**`toto.sso_master`** — Full OpenID Connect 1.0 provider:
- `SSOClient` — client registration: `client_id`, hashed `client_secret`, allowed redirect URIs, allowed scopes, `trusted` flag (skips consent screen for first-party apps).
- `SSOSubject` — stable UUID-based `sub` claim (never exposes the raw `User.pk`).
- `SSOAuthorizationCode` — short-lived (5 min) one-time code issued at `/sso/authorize/`, consumed at `/sso/token/`. Supports PKCE (`code_challenge` + `code_challenge_method`).
- `SSOAccessToken` — opaque bearer token (1-hour TTL) for `/sso/userinfo/`.
- `SSOSigningKey` — RSA key pair. Public key in plaintext for JWKS responses; private key stored as a `gervazy.EncryptedPrivateKey`. Decryption at runtime uses `SSO_VAULT_PASSWORD` from the environment.
- `SSORelyingParty` — proxy model alias for `SSOClient` used in the onboarding/provisioning workflow.

---

### Community (base)

**`toto.socialhub`** — Community model and social layer.
- `Community` — a named organization (Guild, Company, Non-Profit, Family, Other). Has a head person, senior members (M2M), territory, address, federation, email service, `is_autonomous`, `is_foreign`, and `is_federal_tribe` flags. A `parent` self-FK records the community hierarchy (parent → child chain). Only admin can set or change the parent.
- `CommunityNewsPost` (extends `AbstractSection`) — rich Trix-body posts attached to a community, tagged with `CommunityNewsTopic` tags. Visibility is `public` or `community-only`.
- `MembershipApplication` — email-verified join request to a community. Starts with a 6-digit code sent to the applicant's email; status moves through `pending → verified → endorsed → invited → rejected`.
- `ReferenceRequest` — a member endorses an applicant. When a reference is accepted, the system activates the user account and adds the `Person` to the community in a single atomic save.
- `EmailService` re-exported here for backward compatibility.
- **Administrata** — a Cytoscape-based community chain view (`/socialhub/communities/<slug>/administrata/`), visible only to `Person.is_federal_agent` users. Shows the full hierarchy of all communities as rounded-rectangle nodes with parent→child arrows, and each community's head person as a circle node. Backed by a JSON endpoint at `.../administrata/graph.json`.

**`toto.events`** — Scheduled event calendar. `ScheduledEvent` records a named event with start/end time and optional location. Linkable to bazaar products (booking/ticket). `sync_adapters.py` emits graph change events for the ravioli Neo4j layer.

**`toto.polls`** — Informal polling. `Poll` with multiple `PollOption` records; members cast `PollVote`. Separate from `assembly` voting — polls are lightweight and carry no governance effect.

**`toto.assembly`** — Democratic governance engine (the parliament).
- `CommunityAssemblyConfig` — per-community quorum fraction (e.g. 0.51 = simple majority of yes/(yes+no)).
- `AssemblyProposal` — a motion with type (`rule`, `asset_tax`, `emg_declare`, `mag_elect`, `impeach`, `senate_appoint`), open/close timestamps, and senate deadline.
- `AssemblyVote` — one vote per person per proposal (`yes` / `no` / `abstain`). Unique constraint enforced at DB level.
- `AssemblyDecision` — immutable enacted record. Forms a SHA-256 hash-chain: each decision's `content_hash = SHA256(content_json + prev_hash)`. Cannot be updated after creation (`.save()` raises if `pk` is set).
- `CommunityRule` — a named rule enacted by a decision. Can be repealed by a subsequent proposal.
- `CommunityTransactionFee` — blanket or per-asset fee in basis points applied to asset transfers within the community. Can be set by admin or by assembly decision.
- `PollTax` — periodic tax with a head component (flat per-member base units) and a wealth-rate component (bps of member's asset balance). Federal agents and federal-tribe community members are always exempt via `compute_amount_for()`.
- `PollTaxPayment` — records per-person per-period payments. Unique on `(poll_tax, person, period_label)`.
- `CommunitySenate` — optional upper chamber. Senators are an explicit M2M (not the same as `senior_members`). The senate has a configurable `veto_window_days` to block a passed proposal. One veto is sufficient.
- `SenateVeto` — a senator's veto record. `OneToOneField` on the proposal ensures only one veto can block it.

**`toto.magistrate`** — Elected governance officials. Tracks magistrate appointments, oversight domains, and links back to the assembly decisions that created the seat. Exposes a public overview page of current holders.

**`toto.tribunal`** — Dispute resolution and case tracking.
- `TribunalCase` — a complaint or dispute with a title, reason, description, case number, opener, assignee (judge/mediator), and optional links to a `RealWorldObject` or a `bazaar.Order`.
- Case status moves through configurable stages. Evidence, rulings, and case notes are stored as sub-records. Tribunal links to inventory and bazaar to support custody and commercial disputes.

**`toto.mobilization`** — Civic readiness and upstream emergency command. Manages the pipeline from detection to enacted event: `MobilizationReport` (draft → enacted) → `MobilizationEvent` → `EmergencyStatus`. Maintains the `Responder` registry and `AchievementBadge` awards. Emergency declarations require an `assembly.AssemblyProposal` vote before activation.

**`toto.response`** — Field operations layer. Everything that happens after a `MobilizationEvent` is created: `Deployment` (planned → active → completed), `DeploymentAssignment` (per-responder role and status), `Intervention` (discrete field tasks that can mitigate a `detections.Detection`), `EvacuationRoute`, `DeploymentRoute`, `DeploymentEquipment`. Business logic is orchestrated from `mobilization.services`; `response` owns the field-side data.

---

### Economy (base)

**`toto.assets`** — Core ledger and financial primitives.
- `Asset` — a named token. Key fields: `unit_name` (unique ticker), `decimals`, `total_supply_base_units` (integer, never a float), `is_currency`, `backing_document`, `minting_authority`.
- `LedgerAccount` — a named account. Types: `user`, `system`, `reserve`, `external`. Can be linked to a user.
- `AssetHolding` — the current balance of an asset in an account. Balance is stored as a non-negative integer in base units. Unique on `(asset, account)`.
- `LedgerTransaction` — groups one or more entries. Once `posted=True`, immutable. Has a `source_type` / `source_id` generic FK for provenance.
- `LedgerEntry` — a signed integer amount in base units, crediting or debiting one account. Immutable after creation; cannot be deleted. The double-entry invariant (debits = credits per transaction) is enforced by the service layer.
- `LedgerHash` — one-to-one with each posted `LedgerTransaction`. Stores `previous_hash` and `hash = SHA256(tx_data + prev_hash)`, forming an append-only chain for tamper detection.
- `Currency` — ISO-code to `Asset` mapping. One-to-one with the asset it pegs.
- `Obligation` — a debt: `debtor_account` owes `amount_base_units` of `asset` to `creditor_account` by `due_at`. Optional collateral. Status: `pending → overdue → fulfilled / defaulted`.
- `Contract` — a Lapis smart-contract program stored as YAML in `code`. Validated by the Lapis compiler on save. `global_state` JSON holds runtime VM state (counters, flags).
- `Agreement` — a two-party link (`source_account` → `target_account`) governed by a `Contract`. The agreement is the runtime instantiation of a contract between two actors.

**`toto.claims`** — Contract lifecycle primitives. All four models attach to an `Agreement` or `Contract` via FK and optionally carry a `source_type` / `source_id` generic FK for provenance:
- `Entitlement` — a right held by an account (service access, lease right, exercise right, reward eligibility, claim right, usage right). Has `starts_at` / `ends_at` and a status lifecycle.
- `Schedule` — a recurring or one-shot temporal trigger (billing, renewal, vesting, payout, settlement, reward, checkpoint). `next_run_at` is indexed; a Celery beat task polls for due schedules.
- `Condition` — a predicate that must be satisfied before an effect fires. Kinds: time, status, approval, balance, evidence, threshold, manual, external. `expression` JSON encodes the rule; evaluation is handled by the service layer.
- `Allocation` — a reserved or ring-fenced portion of an asset (escrow hold, collateral, margin, vesting pool, staking lock, prepaid balance, budget). Tracks `allocated`, `released`, and `consumed` sub-amounts against the `amount_base_units` ceiling.
- `ContractEvent` — append-only log of what happened under a contract (payment due, payment paid, entitlement granted, condition satisfied, allocation released, default, settlement…). Each event may link to a transaction, obligation, entitlement, schedule, condition, or allocation.

**`toto.contracts`** — Contract graph editor and human-readable document layer.
- `Contract` — a named contract with an optional YAML snapshot (`code`, validated as `language: lapis`, `kind: claims_mesh`). This is the human-authored description layer on top of `assets.Contract`.
- `ContractNode` — a node in the contract graph with a `node_type` (contract, obligation, entitlement, schedule, condition, allocation, event, ledger_account, asset, agreement, and more). Nodes can be manually authored or backed by a real Django object via `(object_app, object_model, object_id)` generic FK. Position stored for Cytoscape layout.
- `ContractEdge` — a directed edge between two nodes with a typed relationship (`explains`, `grants`, `creates_duty`, `triggers`, `gates`, `allocates`, `settles`, `records`, `debtor`, `creditor`, `uses_asset`, and others).

**`toto.instruments`** — Financial instruments, each backed by the `assets` ledger:
- `FinancialInstrument` — base record: reference, type, status, optional linked `assets.Contract` and `LedgerAccount` (contract/vault/margin account).
- `EscrowContract` — buyer deposits into an escrow account; release or refund to seller/buyer after a condition is met. Status: `draft → funded → released / refunded / disputed`.
- `ForwardContract` — bilateral commitment to deliver `quantity` of `underlying_asset` for `payment_amount` of `payment_asset` at `settlement_at`. Physical or cash settlement.
- `FutureContract` / `FutureMarket` / `FutureMarginPosition` — standardized futures with margin requirements in bps. `FutureMarket` defines contract size, settlement asset, and margin rates. `FutureMarginPosition` tracks required vs deposited margin and margin-call state.
- `OptionContract` — call or put, European or American, physical or cash settlement. Buyer holds the right; writer has the obligation. Premium paid upfront.
- `RevenueShareContract` / `RevenueShareRecipient` — routes revenue from a `revenue_account` to multiple recipients in basis-point shares. Recipients must sum to ≤ 10000 bps.
- `VestingContract` — cliff + linear vesting of an asset from grantor to beneficiary. Tracks `released_amount` against `total_amount`. Release frequency configurable.
- `StakingPosition` — a user stakes `staked_amount` of `staked_asset` into a pool account and earns `reward_asset` at `reward_rate_bps`. Optional lock until.
- `SubscriptionContract` / `SubscriptionPayment` — recurring billing: subscriber pays `amount` per `billing_cycle` to provider. `next_billing_at` is indexed. A Celery beat task runs hourly to collect due payments.
- `LeaseContract` — a lessor leases an asset to a lessee for a `fixed_fee` per `billing_period`. Revenue flows to a `revenue_account`. Status lifecycle mirrors subscription.
- `AmortizationContract` / `AmortizationEntry` — progressive consumption of a pool of asset from `source_account` to `destination_account`. Each `AmortizationEntry` records one amortization step with a ledger transaction reference.

**`toto.bourse`** — Peer-to-peer asset exchange (trader desk).
- `AssetExchangeRequest` — one user offers `offer_amount` of `offer_asset` in exchange for `request_amount` of `request_asset` at a given `exchange_rate`, with optional `commission_percent`. The counterparty accepts and the ledger swap executes atomically. Status: `pending → accepted / rejected / cancelled`.

**`toto.bazaar`** — Full e-commerce marketplace with community and ledger integration.
- `Shop` — belongs to a `Community`. Has a `LedgerAccount` for revenue collection, a `currency`, and a `default_language`. Slug auto-generated.
- `Vendor` — a person selling inside a shop. Has a status lifecycle (`draft → pending_review → active → suspended → archived`), territory FK, and a `verified_at` timestamp.
- `ProductCategory` — hierarchical (self-FK `parent`). Scoped to a shop.
- `Product` — 7 types: `physical`, `digital`, `service`, `booking`, `subscription`, `bundle`, `quote_based`. Has variants, images, stock tracking, delivery zones (M2M to `locations.Zone`), origin/pickup addresses, and an optional `asset` FK (for token-backed products). `is_regulated` + `custodian` + `custodian_approval_status` implement a custodian approval gate.
- `MarketCustodian` — an entity that regulates product types or categories in a shop. Scope: `all`, `type`, or `category`. Regulated products require custodian approval before publishing; service-type orders require custodian review before payment release.
- `Cart` / `CartItem` → `Order` / `OrderItem` — full purchase flow. Orders snapshot product name and SKU at time of purchase. `Order` links to a `LedgerAccount`, `Asset`, and optional `Obligation` for ledger-backed payment.
- `ServiceDelivery` — escrow-like confirmation for service-type order items. Status: `pending → confirmed / disputed → custodian_review → approved / rejected`.
- `PaymentIntent` / `PaymentTransaction` — abstracts payment providers (Stripe, PayPal, bank transfer, Algorand, internal credit, manual).
- `Shipment` / `ShippingMethod` — physical fulfillment with carrier, tracking number, and status lifecycle.
- `Coupon` / `OrderDiscount` — percentage, fixed, or free-shipping discounts.
- `WalletPin` — stores a user's wallet PIN as a `gervazy.EncryptedSecret`, one-to-one with the user.
- `ProductReview` / `VendorReview` — moderated ratings attached to orders.
- `Wishlist` / `WishlistItem` — per-user, per-shop saved products.
- `ShipmentLocation` — reusable delivery addresses for quick checkout.

**`toto.detections`** — Detection events and alert management. `Detection` records a named anomaly or security event with severity, source, and description. Linked to kanban tasks for mitigation tracking and to locations for geo-tagged incidents.

**`toto.logistics`** — Physical package and shipment tracking independent of the bazaar. `Package` → `Shipment` → `Transport` across `Route` segments. Complements bazaar fulfillment for logistics-heavy communities.

**`toto.inventory`** — Real-world object registry and tokenization anchor.
- `ObjectType` — a named category of physical objects (e.g. "Artwork", "Vehicle", "Real Estate").
- `RealWorldObject` — a specific named physical object with owner, description, location, and object type. The anchor for `assets.Tokenization`: once tokenized, the object's `Asset` represents it on the ledger. Also linkable to `TribunalCase` (disputes over custody or valuation).

---

### Knowledge and Learning (base)

**`toto.verbena`** — Abstract content model library shared by all content-bearing apps.
- `AbstractTag` — slug + name, unique. Extended by `CommunityNewsTopic`, etc.
- `AbstractPage` — title, slug, description, `created_at`. Extended by `palimpsest.Page`, `kanban.DocumentationPage`, `academy.Script`.
- `AbstractSection` — Trix rich-text `content` body, `author` FK to `Person`, `order`. Extended by all section-style content in palimpsest, kanban, socialhub, and academy.

**`toto.palimpsest`** — General-purpose wiki and publishing layer.
- `Page` (extends `AbstractPage`) — a standalone article or document page visible within the platform. Pages are the base content unit: essays, field notes, handouts, module notes.
- Sections are rich Trix content blocks ordered within a page.
- Used by `academy` (module notes, handout pages) and by kanban (documentation pages for missions).

**`toto.memo`** — Flashcard and slide deck system.
- `MemoDeck` — a titled, slug-keyed, tagged collection of cards. Used as lecture material in academy lessons and for company presentations.
- `MemoCard` — ordered content card with a Trix body, optional image, and optional `MemoDiagram`.
- `MemoDiagram` — an SVG file referenced from the vault (`VaultFile` with `file_type="svg"`), embeddable in cards.

**`toto.quizzes`** — Quiz and exam engine. `Quiz` → `Question` → `Answer`. Official quizzes (`is_official=True`) are assigned as module exams in `academy.CourseModule`. Quiz attempts are tracked per student; results determine badge awards in the academy.

**`toto.competence`** — Skill badge registry. `SkillBadge` is a named, typed credential awarded when a student completes an academy module exam. Badges aggregate into `LearningPath` progressions in the academy.

**`toto.academy`** — Learning management system.
- `Teacher` — wraps a `Person` with a title and bio.
- `Course` → `CourseModule` → `Lesson` — three-level content hierarchy. Each `CourseModule` unlocks exactly one `SkillBadge` and has an optional exam `Quiz`. Each `Lesson` is backed by a `MemoDeck` as the lecture content.
- `Script` / `ScriptSection` — additional instructional pages per module (multiple allowed), extending `AbstractPage` / `AbstractSection`.
- `Student` — wraps a `Person`. Enrolls in courses (through `CourseEnrollment`), earns badges (through `StudentBadge`), and joins cohorts (`CohortMembership`).
- `CourseEnrollment` — on `completed_at`, automatically creates a `Certificate`.
- `Certificate` — a credential linked to a person and a course (or standalone exam). UUID-keyed for verification.
- `Cohort` — a scheduled group run of a course with a teacher, capacity, and start/end times.
- `LearningPath` — an ordered sequence of `SkillBadge` milestones. Students progress through the path as they earn badges.

**`toto.library`** — Bibliographic reference management. `Book` / `Article` records with BibTeX import/export. Optional vault file attachments for PDFs or scanned documents. Used by researchers and academy content authors.

**`toto.bento`** — Structured note and idea container. `Box` → `BentoItem` (a typed block: text, link, image, quote, reference). A flexible catch-all for collecting ideas, concepts, sources, and relationships that don't fit the stricter content models of palimpsest or memo.

---

### Project Management (base)

**`toto.kanban`** — Project and task management with ledger-backed compensation.
- `Project` — has a project lead (`Person`), a set of `Column` states, and a `ProjectTokenization` (optional asset representing the project on the ledger).
- `Campaign` — groups missions by name, date range, owner, and optional geographic `Zone`.
- `Mission` — a discrete work unit under a campaign. Has `urgency` and `impact` ratings (1–3 scale), optional address and route.
- `Sprint` — a time-boxed iteration within a project.
- `Column` — a kanban state (e.g. Backlog, In Progress, Done). Has `auditors` (practitioners who may move tasks into this column).
- `Task` — a unit of work inside a column and sprint, assigned to a `Practitioner`, with optional reviewer. Weight is Fibonacci-scaled (1/2/3/5/8). `completed_at` is set when moved to a done column.
- `Practitioner` — a `Person` in a project role (contributor, reviewer, auditor, manager, observer). Has a `default_income_account` FK to `assets.LedgerAccount` for salary/allowance receipt.
- `ProjectCommitment` — links a practitioner to a project with an hours/day commitment and date range.
- `PractitionerAllowance` — defines compensation (per diem, hourly, fixed, travel, meal, other) paid from a payer `LedgerAccount` to a recipient account. A Celery beat task runs daily at 17:00 on weekdays to create obligations for active allowances.
- `DocumentationPage` / `DocumentationSection` — rich documentation attached to a mission. Can be flagged as a how-to manual.
- `ProjectTokenization` — links a `Project` to an `Asset` one-to-one. Permanent and immutable (delete is blocked). Acts as project shares on the ledger.

**`toto.mobilization`** — Upstream emergency command layer. `MobilizationReport` → `MobilizationEvent` → `EmergencyStatus`. Owns the `Responder` registry and `AchievementBadge` awards. Interfaces downward into `response` for field operations.

**`toto.response`** — Field operations. `Deployment` → `DeploymentAssignment` / `Intervention` / `DeploymentRoute` / `DeploymentEquipment`. Each deployment belongs to a `MobilizationEvent` and optionally links to a `kanban.Mission`.

---

### Geography (base)

**`toto.locations`** — GIS-backed geographic model layer. Uses PostGIS SRID 4326 (WGS84 / OpenStreetMap).
- `Address` — a physical address with structured fields (country, state/province, locality, street, building, apartment) and an optional `PointField` geometry. Used as a residence by `Person`, a location by `Community`, a shop origin/pickup by `bazaar.Product`, and a mission site by `kanban.Mission`.
- `Territory` — a named `PolygonField` representing an administrative or governance area. Has an optional capital `Address`. Used by `socialhub.Community` and `bazaar.Vendor` territory assignments.
- `Zone` — a named `MultiPolygonField` subdivision within a territory. Used for delivery zones in `bazaar.Product` and campaign scoping in `kanban.Campaign`.
- `Route` / `RouteChain` — a `MultiLineStringField` path between an origin and destination, grouped into ordered chains. Used by logistics and by mission routing.
- `MapLayer` / `MapLayerPolygon` — heat-map-style thematic layers. Each layer has a unit, value range, color-scale style config, and optional inversion/half-range flags. Polygons carry a float value and center point for labelling.
- `geocode.py` — Nominatim-backed address lookup (geocoding + reverse geocoding). Respects the `LOCATIONS_GEOCODING` settings dict.

---

### SSO (base)

**`toto.sso_core`** — Shared OIDC manifest: scope definitions, claim mappings, and the `SSOManifest` structure used by both the provider and any client.

**`toto.sso_master`** — Full OIDC 1.0 provider (described in detail in Identity section above).

---

## Studio Apps (BUILD_STUDIO=1 only)

The following apps are only installed and routed when `BUILD_STUDIO=1`. They all require ASGI and either WebSockets, Neo4j, or a compute kernel.

**Additional third-party libraries added in studio mode:**

**`channels`** — Django Channels. Replaces the WSGI request-response loop with an ASGI application that handles both HTTP and WebSocket connections. Backed by `channels_redis.core.RedisChannelLayer` connected to Redis.

**`jsoneditor`** — A JSON editor widget for the Django admin used by studio apps (workflows, mandragora) where interactive JSON config editing is needed beyond what `django_json_widget` provides.

---

**`toto.enigma`** — Real-time group chat over WebSockets.
- `Room` — a named chat room with a slug, participants (M2M to `User`), and people (M2M through `Participant` to `Person`).
- `Participant` — the through-model linking a `Person` to a `Room`. Carries a `joined_at` timestamp and `is_active` flag. Only human participants (person required) are allowed.
- `consumers.py` — a Django Channels WebSocket consumer. Joins a room group on connect, broadcasts messages, and records history.
- `api_views.py` — REST endpoints for room listing and participant management.
- `middleware.py` — WebSocket authentication middleware.

**`toto.ravioli`** — The sole Neo4j boundary. Enabled only in studio mode because Neo4j is only deployed in `portal_max`.
- `CypherQuery` / `CypherQueryResult` — admin-authored Cypher queries stored in Postgres, executed against Neo4j on demand, results stored as JSON node/edge lists for visualization.
- `GraphChangeEvent` — the event queue. Every app that syncs to Neo4j writes events here (`upsert_node`, `delete_node`, `resync_links`, `upsert_junction`, etc.) via Django post-save/post-delete signals. A Celery worker drains the queue.
- `GraphProjectionPlan` — a batch migration plan with a scope dict, diff summary, and apply status. Used when resyncing large slices of the graph.
- `GraphSync` — a `managed=False` proxy model that exposes a global admin action button for triggering a full sync.
- `connection.py` — the raw Neo4j Bolt driver connection. Only imported inside ravioli; no other app ever imports this.
- `loader.py` — reads the YAML graph schema to know which models map to which node labels and which FK fields map to which relationship types.
- `scaffold.py` / `sync.py` — apply the graph shape (upsert nodes and relationships) to Neo4j.

**`toto.texlab`** — Asynchronous LaTeX compilation with live streaming.
- `TexWorkspace` / `TexFile` — a workspace is a named collection of `.tex` / `.bib` / asset files. The main file is compiled.
- `TexBuildJob` — a compilation run. Triggered via the UI, queued to Celery, compiled with `pdflatex`/`latexmk`, and output streamed back via WebSocket.
- `consumers.py` — a Channels consumer subscribed to a build job's group; pushes log lines and final status to the browser in real time.
- `compile.py` — the actual compilation subprocess logic.
- `predefined_tasks.py` — Celery task definitions for asynchronous compilation runs.

**`toto.mandragora`** — Jupyter-style interactive compute engine.
- `ComputeKernel` — a named Python kernel with environment variables, timeout, and `auto_close` flag. Backed by a subprocess.
- `KernelDependency` — a pip package required by a kernel. Status: `pending → installing → installed / failed`. Installation is async.
- `Notebook` → `Cell` — a notebook is an ordered collection of code (Python) or markdown cells. Each cell has `stdout`, `stderr`, `execution_count`, and `rich_output` (for plots, DataFrames, HTML).
- `kernel_server.py` — a standalone ZMQ server (bound at `tcp://*:5555`) that the Django web process talks to over `KERNEL_SERVER_ADDR`. The kernel server manages Python subprocesses and forwards execution requests/responses. It runs as a separate Docker service (`kernel_server`).
- `consumers.py` — Channels consumer that proxies cell execution to the kernel server over ZMQ and pushes output back to the browser cell-by-cell in real time.

**`toto.workflows`** — Visual DAG automation engine.
- `Workflow` → `WorkflowNode` → `WorkflowEdge` — a directed acyclic graph. Node types: `lambda` (runs Python code in mandragora), `split` (fan-out), `join` (fan-in), `report` (renders output), `predefined_task` (calls a registered Celery task by name).
- `LambdaFunction` — a Python code cell backed by a `mandragora.ComputeKernel`. Executed when a lambda node runs.
- `ReportTemplate` — a JSON visualization schema supporting `table`, `chart` (bar/line/area/pie), `card`, and `text` block types. Validated on save.
- `WorkflowRun` → `WorkflowNodeRun` → `WorkflowEdgeRun` — full execution trace. Each node run carries input/output data, status, error, and Celery task ID.
- `Report` / `ReportPage` — the output artifact. A report is a rendered snapshot of a report template filled with data from a workflow run.

**`toto.weather`** — Weather data ingestion and display.
- Ingests current conditions and multi-day forecasts for tracked locations (linked to `locations.Address`).
- `WeatherObservation` records temperature, humidity, wind speed, pressure, and condition code. Observations are fetched on a schedule.
- `workflows.py` — integrates with the workflow engine to trigger forecast jobs and push results to reports.

**`toto.sketch`** — Collaborative real-time whiteboard.
- `Board` — a named canvas with metadata.
- `consumer.py` — a Channels WebSocket consumer. Connected clients send draw operations (path, shape, text, erase) as JSON; the consumer broadcasts to all other members of the board's group and persists a compact event log.
- `routing.py` — WebSocket URL routing for board connections.

**`toto.ocr`** — Document OCR processing pipeline.
- `OCRProject` / `OCRDocument` / `OCRPage` — a project groups documents; each document is split into pages for processing.
- `ocr.py` — submits page images to the OCR backend (Tesseract or a cloud API).
- `transform.py` — post-processing: layout analysis, column detection, table extraction, markdown conversion.
- Results are stored per page and can be exported or fed into further processing (memos, palimpsest pages).

**`toto.steven`** — AI agent management.
- `Agent` — a named autonomous agent with a system prompt, model selection, tool configuration, and run history.
- `AgentRun` — one invocation of an agent. Tracks input, output, status, token usage, and error.
- Agents can be triggered manually from the dashboard or scheduled via the workflow engine.

**`toto.travels`** — Route journeys and travel history.
- `Trip` — a named journey linked to a `Person`. Has start/end dates and a sequence of visited `locations.Address` stops.
- `TripSegment` — one leg of a trip, optionally linked to a `Route`.
- `VisitReview` — a person's review and rating of a visited address or place.
- Integrated with the locations map for visual route display.

---

### Simulation (regis — separate project)

**`regis`** — A separate Django project (`regis/`) for geophysical and macroeconomic simulation. Not installed in the portal deployment; runs as its own stack.

- **`regis.geophysics`** — Planet generation engine. `Planet` records define biome distributions, terrain parameters, and optimization targets. `planet.py` / `biomes.py` generate synthetic geography. `optimization.py` runs gradient-based fitting to match target distributions.

- **`regis.economy`** — Full macroeconomic simulation with modular subsystems. Each subsystem is a separate module in `economy/simulation/`: `cohorts` (demographic age groups), `construction` (infrastructure build rates), `extraction` (resource yield), `fiscal` (tax and budget flows), `forecast` (multi-step projection), `governance` (policy rules), `infrastructure` (capacity and decay), `labor` (employment and wages), `market` (price discovery and exchange), `planet` (geophysical inputs), `population` (birth, death, migration), `production` (output functions). The `economy.tasks` Celery module can trigger simulation runs asynchronously. `economy.plugins.profile_plugins` registers community economic profiles that the simulation engine can use as parameter sets.

  Used for research and pre-deployment modelling of policy changes (tax rates, infrastructure investment, demographic shocks) before committing them to the live platform.

---

## Relations

```
Federation
  └── many Community (socialhub)
        ├── optional parent Community (self-FK, hierarchy)
        ├── one AssemblyConfig / Senate
        ├── many AssemblyProposal → AssemblyDecision (hash-chain)
        │     └── enacts CommunityRule / CommunityTransactionFee / PollTax
        ├── many Person (members)
        │     ├── one User (auth)
        │     ├── one UserStrongbox (gervazy, optional)
        │     │     └── VaultMasterKey → WrappedDataKey
        │     │           └── EncryptedSecret / EncryptedFile / EncryptedPrivateKey
        │     └── one LedgerAccount (assets)
        │
        └── many Shop (bazaar)
              ├── Vendor → Product → Cart → Order → Payment
              └── MarketCustodian (regulates product types)

Asset (assets)
  ├── AssetHolding → LedgerAccount
  ├── LedgerTransaction → LedgerEntry → LedgerHash (chain)
  ├── Obligation (debtor ↔ creditor LedgerAccount)
  ├── Contract (Lapis YAML) → Agreement (source ↔ target)
  │
  ├── Tokenization → RealWorldObject (inventory)
  └── ProjectTokenization → Project (kanban)

FinancialInstrument (instruments)
  ├── one-of: Escrow / Forward / Future / Option / RevenueShare
  │          / Vesting / Staking / Subscription / Lease / Amortization
  └── → assets.Contract (Lapis program)
       → assets.Obligation (per role)

Person
  ├── Practitioner (kanban) → Task → Mission → Campaign → Project
  ├── Teacher / Student (academy) → Course → Module → Lesson (MemoDeck)
  ├── Participant (enigma) → Room
  └── opened TribunalCase

ravioli (Neo4j)
  ← GraphChangeEvent (from signals across all apps)
  → Neo4j nodes + relationships (graph queries, CypherQuery)

sso_master
  → SSOSigningKey.encrypted_key → gervazy.EncryptedPrivateKey
```

---

## Summary

toto is a platform-scale civic and economic operating system for communities. Its distinguishing combination: a tamper-evident double-entry ledger (with hash-chaining and immutable entries), hierarchical encryption-at-rest for user secrets, a democratic governance engine that writes enacted decisions into an append-only hash-chain, and a graph layer in Neo4j for relationship queries — all inside a single Django monorepo. The marketplace, project management, academy, and chat features make it livable day-to-day, while the financial instruments (escrow, futures, options, vesting, subscriptions, leases) give communities the tools to build real economic relationships between members. The simulation project (`regis`) provides a parallel environment for modelling the economic dynamics of such communities before committing changes to the live system.

---

## Portal vs Studio: Deployment Split

The repository contains one codebase but ships as two distinct deployment targets. The switch is a single environment variable: `BUILD_STUDIO`. The `deploy.py` script reads a YAML config file and uses this value to generate the appropriate `docker-compose.yaml` and `.env`.

### The Switch

```python
# portal/portal/settings.py
if BUILD_STUDIO:
    ASGI_APPLICATION = "portal.asgi.application"
else:
    WSGI_APPLICATION = "portal.wsgi.application"
```

When `BUILD_STUDIO=0` the process model is synchronous WSGI (gunicorn). When `BUILD_STUDIO=1` it becomes asynchronous ASGI (daphne/uvicorn) — a meaningful difference: WebSocket connections require ASGI, so the switch is not cosmetic.

---

### Portal (BUILD_STUDIO=0) — `portal_mini`

The base deployment. Serves the complete financial, governance, identity, and community stack but drops every real-time and compute feature.

**Server:** gunicorn (WSGI, synchronous workers)

**INSTALLED_APPS additions over plain Django:**
- All base apps listed in the Apps section above (core through sso_master)
- Framework integrations: daphne (installed but not used as server), django_prometheus, corsheaders, rest_framework, colorfield, reversion, django_jsonform, markdownx, trix_editor

**What works in portal:**
| Feature | Available |
|---|---|
| User identity + OIDC provider | ✓ |
| Gervazy vault + encrypted secrets | ✓ |
| Ledger, assets, obligations | ✓ |
| Lapis contracts + contract graph editor | ✓ |
| Financial instruments (all 10 types) | ✓ |
| Marketplace (bazaar) | ✓ |
| Democratic governance (assembly, tribunal) | ✓ |
| Project management (kanban) | ✓ |
| Academy / LMS | ✓ |
| Knowledge base (verbena, memo, library) | ✓ |
| GIS / locations | ✓ |
| Celery beat (allowances, subscriptions) | ✓ (needs broker) |
| WebSockets / live chat | ✗ |
| Neo4j graph layer | ✗ |
| Compute kernels (Jupyter-style) | ✗ |
| LaTeX compilation (texlab) | ✗ |
| Collaborative whiteboard (sketch) | ✗ |
| AI agent management (steven) | ✗ |
| Workflow engine | ✗ |

**Infrastructure minimum:**
```
Postgres (PostGIS extension)
Redis          — Celery broker + result backend
Nginx          — reverse proxy / static files
```

**Config file:** `configs/portal_mini.yaml`
```yaml
BUILD_STUDIO: "0"
http_port: 8081
services:
  websockets: false
  neo4j: false
  celery: true      # beat schedule still runs
  prometheus: false
```

---

### Studio (BUILD_STUDIO=1) — `portal_max`

The full deployment. Everything from portal plus real-time, graph, and compute capabilities.

**Server:** uvicorn behind daphne (ASGI, async workers)

**Additional INSTALLED_APPS (only when BUILD_STUDIO=1):**
```python
["channels", "jsoneditor",
 "toto.enigma", "toto.ravioli", "toto.texlab", "toto.mandragora",
 "toto.workflows", "toto.weather", "toto.sketch", "toto.ocr",
 "toto.steven", "toto.travels"]
```

**What studio adds over portal:**

| Feature | App | Detail |
|---|---|---|
| WebSocket chat | `enigma` | Room + Participant, Django Channels consumer |
| Collaborative whiteboard | `sketch` | Real-time canvas, Channels consumer |
| LaTeX compilation | `texlab` | WS-based compile jobs, returns PDF/PNG |
| Compute kernels | `mandragora` | Jupyter-style Notebook/Cell, ZMQ backend |
| Neo4j graph layer | `ravioli` | GraphChangeEvent drain → Neo4j upserts |
| Cypher queries | `ravioli` | CypherQuery, GraphProjectionPlan, GraphSync |
| Workflow engine | `workflows` | DAG-based task orchestration |
| Weather data | `weather` | Forecast ingestion, location-linked |
| OCR pipeline | `ocr` | Document image → text extraction |
| AI agent management | `steven` | Agent + AgentRun, model/tool config |
| Travel journeys | `travels` | Trip + TripSegment + VisitReview |

**Additional settings injected when BUILD_STUDIO=1:**
```python
RAVIOLI_ENABLED = True
NEO4J_URI      = "bolt://neo4j:7687"
NEO4J_USER     = "neo4j"
NEO4J_PASSWORD = ...

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [("redis", 6379)]},
    }
}

KERNEL_SERVER_ADDR = "tcp://kernel_server:5555"   # ZMQ
```

**Infrastructure required:**
```
Postgres (PostGIS)
Redis                   — Celery broker + Django Channels channel layer
Neo4j 5 + APOC plugin   — ravioli graph database
Kernel server           — ZMQ tcp://kernel_server:5555 (mandragora)
Nginx                   — reverse proxy, WS upgrade
Prometheus + Grafana    — metrics (optional but included in portal_max.yaml)
Loki + Promtail         — log aggregation (optional but included in portal_max.yaml)
```

**Config file:** `configs/portal_max.yaml`
```yaml
BUILD_STUDIO: "1"
services:
  websockets: true
  neo4j: true
  celery: true
  kernel_server: true
  prometheus: true
  grafana: true
  loki: true
```

---

### Shared Celery Beat (both tiers)

Even in base (portal) mode the Celery beat schedule is active if a broker is configured:

```python
CELERY_BEAT_SCHEDULE = {
    "pay-daily-allowances": {
        "task": "toto.kanban.tasks.pay_daily_allowances",
        "schedule": crontab(hour=17, minute=0, day_of_week="1-5"),
    },
    "process-due-subscriptions": {
        "task": "toto.instruments.tasks.process_due_subscriptions",
        "schedule": crontab(minute=0),
    },
}
```

Daily allowances are posted at 17:00 on weekdays; subscription billing runs every hour. These run identically in both tiers.

---

### How deploy.py Generates the Stack

`deploy.py` is the entry point for building any deployment. It reads a YAML config and emits `docker-compose.yaml` + `.env`:

```
deploy.py --config configs/portal_mini.yaml   →  gunicorn WSGI stack
deploy.py --config configs/portal_max.yaml    →  uvicorn ASGI stack + neo4j + kernel
```

The script conditionally adds Docker services based on the config:
- `BUILD_STUDIO=1` → uses `uvicorn` command, adds `neo4j`, `kernel_server`, `celery_worker` services
- `BUILD_STUDIO=0` → uses `gunicorn` command, omits those services
- `services.prometheus: true` → adds `prometheus`, `grafana`, `loki`, `promtail` services

The `settings_builder.py` shared module mirrors this logic on the Python/Django side: it reads the same YAML keys and injects the appropriate `INSTALLED_APPS`, `CHANNEL_LAYERS`, `NEO4J_*`, and `KERNEL_SERVER_ADDR` settings automatically so the YAML config remains the single source of truth for both the Docker topology and the Django runtime.
