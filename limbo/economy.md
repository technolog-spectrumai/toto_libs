# Economy — Limbo Archive Notes

This document describes the economic, financial, governance, and operational apps
currently archived in `toto/limbo/`. They were built as a coherent system and are
documented together because they only make sense as a whole. Reintroducing one layer
typically requires the layers below it.

---

## The Idea

A community on toto was meant to operate like a mini-state: it issues its own assets,
levies taxes, votes on rules, runs a marketplace, settles disputes, and compensates
contributors from a shared ledger. Everything economically meaningful is tokenised and
tracked on a double-entry ledger. Governance decisions are hash-chained and immutable.
The whole stack can be used for a DAO, a cooperative, a studio, an emergency
response network, or just a tightly-run internal organisation.

---

## Layer 1 — The Ledger (`assets`)

The foundation. Everything else settles here.

- **Asset** — a named token with decimals and total supply (inspired by Algorand ASA)
- **LedgerAccount** — a named account that holds balances
- **AssetHolding** — how much of an Asset a LedgerAccount currently holds
- **LedgerTransaction + LedgerEntry** — immutable double-entry: every balance change
  posts a debit + credit pair. Sum of all entries per asset is always zero.
- **LedgerHash** — SHA-256 chain linking every posted transaction to the previous one.
  Tamper evidence at the DB level. `verify_hash_chain()` re-verifies the whole chain.
- **Obligation** — a recorded debt between two accounts, to be settled by future
  transactions. Used by magistrate fines, payroll, loans.
- **Contract + Agreement** — the runtime layer for Lapis smart contracts. A YAML-based
  VM validates state transitions before they hit the ledger.
- **Currency** — a display/exchange denomination separate from the ledger asset.
- **Tokenization** — a OneToOne link between a physical `inventory.RealWorldObject` and
  an on-ledger `Asset`. Enables collateralisation of real property.

**Backend swap point:** the ledger has an abstract `LedgerBackend`. Swap Django/Postgres
for Algorand or Solana by subclassing and setting `ASSETS_BACKEND` in settings.

---

## Layer 2 — Financial Instruments (`claims`, `instruments`, `contracts`)

Structures on top of the ledger.

### `claims` — Contract lifecycle primitives

Four models attached to an `assets.Agreement`:

- **Entitlement** — a right held by an account (service_access, lease_right, exercise_right…)
- **Schedule** — a temporal trigger (once, daily, monthly…) for billing, vesting, payout
- **Condition** — a predicate gating an effect (time, balance, approval, external…)
- **Allocation** — ring-fenced reserve (escrow_hold, collateral, vesting_pool, staking_lock…)
- **ContractEvent** — append-only audit log of every lifecycle event

### `instruments` — Financial instruments

Ten deterministic instrument types, each with a service class that drives state via claims:

| Instrument | Use case |
|---|---|
| Escrow | Safe two-party exchange; funds released on condition |
| Forward | Agreed future price for an asset at a set date |
| FutureMarket / FutureContract | Standardised futures trading |
| FutureMarginPosition | Leveraged futures with margin maintenance |
| RevenueShare | Fractional revenue distribution to multiple recipients |
| Timelock | Locked funds released after a time period |
| VestingContract | Token vesting schedule for contributors |
| StakingPosition | Locked stake earning rewards |
| OptionContract | Right (not obligation) to buy/sell at strike price |
| AmortizationContract | Scheduled loan repayment with principal + interest |

### `contracts` — Visual contract authoring

A Cytoscape.js-backed graph editor for authoring claims meshes as visual documents.
`ContractNode` types: obligation, entitlement, schedule, condition, allocation, ledger_account,
asset, event, agreement, manual note. `ContractEdge` types: grants, creates_duty, triggers,
gates, requires, allocates, secures, settles, debtor, creditor, uses_asset.

---

## Layer 3 — Billing Infrastructure (`tariffs`, `taxes`, `invoice`)

### `tariffs` — Metered billing

Used by studio apps (vault, steven, transcription, ravioli, vod) to charge usage.

- **BillingUnit / BillingMetric** — canonical metric descriptors (e.g. `storage.request`, `ai.input_tokens`)
- **Tariff / TariffItem** — price list: metric → asset → price per unit
- **UsageRecord / UsageCharge** — records actual usage; charges are ledger entries
- **TariffApplication / TariffApplicationLine** — converts a usage statement YAML into an invoice
- The `metering` app was a thin `is_installed`-guarded facade over tariffs.

> **Note:** `toto.quota` (still active) replaced tariffs for the basic track/block use case.
> Tariffs adds *financial settlement* (ledger debit) on top of quota enforcement.

### `taxes` — Transaction fees

- **TransactionFeePolicy** — fee rate (basis points) or fixed amount on a specific asset's transactions
- **TransactionFee** — materialised fee record for a specific `LedgerTransaction`

### `invoice` — Billing documents

Invoice, InvoiceLine, BillingCycle, InvoiceReport. Wired to vault buckets and tariff applications.

---

## Layer 4 — Peer Economy (`bourse`, `subscriptions`, `payroll`, `loans`, `insurance`, `leasing`, `mission_economy`)

### `bourse` — OTC exchange desk

Peer-to-peer asset swap requests. Member posts "I offer X of asset A for Y of asset B."
Counterparty accepts; service validates balances and posts both sides atomically.

### `subscriptions` — Subscription billing

SubscriptionPlan, SubscriptionPrice, SubscriptionCustomer, Subscription, SubscriptionInvoice,
SubscriptionPayment. Drives VOD collection access gating and academy course gating.

### `payroll` — Contributor compensation

Service-only. Reads `mission_economy.PractitionerAllowance`, creates `assets.Obligation`
entries daily (Celery beat at 17:00 on weekdays), fulfils obligations from configured
payer accounts. Supports per-diem, hourly, fixed, travel, and meal allowance types.

### `loans` — Loan management

Service-only. Manages lender/borrower `LedgerAccount` pairs, `assets.Asset` denomination,
`assets.Obligation` creation and repayment tracking.

### `insurance` — Insurance management

Service-only. Premium collection, coverage denomination in `assets.Asset`, claim settlement
via `assets.Obligation`. Structural dependency on `contracts` and `claims`.

### `leasing` — Physical and digital leases

`Lease` model: lessor/lessee accounts, payment asset, optional `contracts.Contract` link.

### `mission_economy` — Kanban ↔ Assets bridge

Created to decouple project management from financial settlement. Named after Mariana Mazzucato.

- **PractitionerIncomeProfile** — default income account for a practitioner
- **PractitionerAllowance** — recurring or fixed compensation rule (payer account → recipient account)
- **ProjectTokenization** — links a kanban `Project` one-to-one to an `assets.Asset` (project equity)

---

## Layer 5 — Commerce (`bazaar`, `logistics`)

### `bazaar` — Community marketplace

Full e-commerce: Shop → Vendor → Product → Cart → Order → Payment → LedgerTransaction.
Physical products link to `inventory.RealWorldObject`. Community transaction fees
(set by assembly vote) applied at checkout. Disputes feed into `tribunal`.

Key flows:
- `pay_order_with_ledger()` — posts ledger entries for payment
- `create_obligation_for_order()` — credit-term orders
- `apply_coupon()` — discount codes with usage limits

### `logistics` — Transport and package tracking

Fleet of `Transport` (vehicle/courier), `Package` (shipment), `PackageEvent` (append-only
status log). Bazaar `Shipment` links OneToOne to a `Package` for end-to-end order tracking.

---

## Layer 6 — Governance (`assembly`, `magistrate`, `tribunal`, `senate`, `capitol`, `treasury`)

The community self-governance stack. Designed for community-governed DAOs.

### `assembly` — Democratic legislature

Proposals → votes → hash-chained `AssemblyDecision` → enacted policy objects:

- **CommunityRule** — a named policy rule with parameters
- **CommunityTransactionFee** — a fee on bazaar transactions (flat or percentage)
- **PollTax** — a periodic membership levy collected automatically by Celery

Every `AssemblyDecision` is SHA-256 hash-chained to the previous decision — same
tamper-evidence pattern as `assets.LedgerHash`. The governance history is append-only
and verifiable.

Optional **CommunitySenate** (upper house) can veto passed proposals within a deadline window.

### `magistrate` — Executive enforcement

Elected via `assembly` proposals. Issues `MagistrateDecision` (advisory/directive/injunction)
and `MagistrateFine` (creates `assets.Obligation` against target's account).

### `tribunal` — Judiciary / dispute resolution

`TribunalCase` → `TribunalParty` → `TribunalClaim` → `TribunalEvidence` → `JurySession`
→ `JuryVote` → `TribunalRuling`. Monetary rulings create `assets.Obligation` for enforcement.
Cases can reference a bazaar `Order` or an `inventory.RealWorldObject`.

### `senate` — View-only upper house UI

No models. A template-and-view layer displaying senate composition. The `CommunitySenate`
model lives in `assembly`.

### `capitol` — Governance dashboard

No models. Aggregates stats from magistrate, assembly, tribunal, treasury in one view.
Provided the community switcher (`community_session.py`) used by senate/assembly/treasury.

### `treasury` — Financial dashboard

No models. Aggregates tariff income, transaction fees, subscription revenue, tax streams
into a single revenue dashboard.

---

## Layer 7 — Operations (`mobilization`, `response`, `incidents`, `detections`, `tactical`, `inventory`, `robots`)

### `mobilization` + `response` — Emergency command

Full upstream→field pipeline:
`Detection → MobilizationReport → MobilizationEvent → EmergencyStatus → Deployment → Intervention`

Emergency declarations require an `assembly.AssemblyProposal(type="emg_declare")` to pass —
governance gates operational emergency activation.

### `detections` — Threat registry

Geographic, severity-scored detection records. Feed mobilization reports.
`mitigation_task` links to a `kanban.Task` for project-management resolution.

### `incidents` — Incident registry

Higher-level incident records above raw detections.

### `tactical` — Operational map

Live map with plugin-based data layers. `FieldMapPlugin` and `FieldMetricsPlugin`
registered by apps that want to contribute to the map. Locations app registered
its geographic data this way.

### `inventory` — Physical asset registry

Catalog of real-world objects (vehicles, equipment, real estate). Can be tokenised
on the ledger via `assets.Tokenization`. `DeploymentEquipment` links items to
response deployments; `EmergencyEquipmentAccess` grants hybrid access under emergencies.

### `robots` — Robot fleet

`Robot` model with OneToOne to `inventory.RealWorldObject`. Command, telemetry, mission,
maintenance log, and status models. Entirely self-contained except for the inventory link.

---

## DAO / Cooperative Use Case

The full stack describes a viable on-chain community organisation:

1. **Founding** — admin mints a community `Asset` (the governance token)
2. **Membership** — `socialhub.Community` + `MembershipApplication`; new members receive
   a token allocation from the reserve account
3. **Governance** — `assembly` proposals voted by token holders; passed proposals
   enact rules, fees, and taxes automatically
4. **Treasury** — community collects `CommunityTransactionFee` on bazaar transactions
   and `PollTax` from members; funds flow into `Community.ledger_account`
5. **Justice** — `tribunal` resolves disputes; `magistrate` enforces day-to-day decisions
6. **Economy** — `bourse` for peer exchange; `bazaar` for commerce; `subscriptions`
   for recurring revenue; `payroll` for contributor compensation; `instruments` for
   structured financial agreements
7. **Audit** — `LedgerHash` chain makes the full financial history tamper-evident;
   `AssemblyDecision` chain makes governance history tamper-evident

---

## Reintroduction Notes

Apps are self-referential and should be reintroduced as a group.

**Minimum viable economy:**
`assets` + `claims` + one or two instrument types + `assembly` (just CommunityRule)

**Full governance DAO:**
All of layers 1–6, plus SSO (already active) for identity

**Emergency operations:**
Layers 1–2 (for compensation) + layer 7 (detections → mobilization → response)

**Caveat:** Migration history in limbo apps references models that were present at
archive time. Fields in active apps may have changed. When reintroducing, audit the
migrations carefully and consider squashing to a fresh initial state rather than
replaying the full history.
