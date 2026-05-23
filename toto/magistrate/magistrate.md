# Magistrate App

Provides fast executive governance for communities: elected magistrates hold time-limited office, issue binding decisions that bypass the normal assembly vote cycle, and are held publicly accountable through a permanent decision ledger.

---

## Core concepts

### What a magistrate is

A magistrate is a `Person` with `is_federal_agent = True` who has been elected to a named `MagistrateRole` within a specific `Community` for a defined term. The election goes through the assembly (`AssemblyProposalType.MAGISTRATE_ELECTION`); when the proposal passes a coordinator confirms it via `elect_confirm`, which creates the `Magistrate` seat.

A person can hold multiple seats (different roles or different communities). Each seat has its own term, status, and decision ledger.

### Oversight domains

Each `MagistrateRole` has boolean flags for the areas it governs:

| Field | Label | Who has it |
|---|---|---|
| `overseeing_mobilization` | Mobilization | Prefect, Legate |
| `overseeing_tribunal` | Tribunal | Tribune |
| `overseeing_trade` | Trade | Quaestor, Aedile |
| `overseeing_finance` | Finance | Quaestor |
| `overseeing_public_order` | Public Order | Prefect, Legate, Aedile, Curator, Praetor |
| `overseeing_legislation` | Legislation | Prefect, Tribune, Censor |
| `overseeing_merchandise` | Merchandise Quality | Aedile |
| `overseeing_education` | Education | Censor |
| `overseeing_relations` | Inter-Community Relations | Propraetor |
| `overseeing_logistics` | Logistics | Curator |
| `overseeing_interior` | Interior & Movement | Praetor |
| `overseeing_productivity` | Productivity & Work | Procurator |

There is also a separate capability flag:

| Field | Meaning |
|---|---|
| `can_set_fines` | May levy infraction fines within their oversight domains. Enabled for Prefect, Quaestor, Aedile. |

`oversight_domains` is a model property that returns the list of active domain labels — used for display only.

### Seeded roles (order matters)

| # | Role | Key domains |
|---|---|---|
| 1 | Prefect | Mobilization, Public Order, Legislation, Fines |
| 2 | Tribune | Tribunal, Legislation |
| 3 | Legate | Mobilization, Public Order |
| 4 | Quaestor | Finance, Trade, Fines |
| 5 | Aedile | Trade, Public Order, Merchandise, Fines |
| 6 | Censor | Education, Legislation |
| 7 | Propraetor | Relations |
| 8 | Curator | Logistics, Public Order |
| 9 | Praetor | Interior, Public Order |
| 10 | Procurator | Productivity |

---

## Data model

### `MagistrateRole`
Admin-managed office definition. Seeded by `ingress_magistrate`. Holds the oversight domain flags and `can_set_fines`.

### `Magistrate`
A filled seat: links `Person + MagistrateRole + Community` with `term_start`, `term_end`, `status`, and an optional back-reference to the election `AssemblyProposal`.

Status lifecycle: `active` → `suspended` / `impeached` / `term_ended`.

`is_active` (property) returns `False` if status ≠ active or `term_end` has passed.

### `MagistrateDecision`
The public decision ledger entry. Created immediately when a magistrate submits any action from the Power Console. Never deleted — only revoked (`status = "revoked"`) or marked reviewed (`status = "reviewed"`). Revocation itself stays on the record.

Decision types map to oversight domains:

| `decision_type` | Domain |
|---|---|
| `mobilization_call`, `emergency_declare` | Mobilization |
| `tribunal_order` | Tribunal |
| `trade_order`, `trade_reversal` | Trade |
| `merchandise_fine` | Merchandise |
| `finance_directive` | Finance |
| `public_order_directive` | Public Order |
| `legislation_fast_track` | Legislation |
| `education_directive` | Education |
| `relations_directive` | Relations |
| `logistics_order` | Logistics |
| `interior_directive` | Interior |
| `productivity_directive` | Productivity |
| `infraction_fine` | Fines |
| `general` | Any |

### `MagistrateReport`
Periodic accountability report submitted by a magistrate to the assembly. Status: `draft` → `submitted` → `acknowledged`. Acknowledgement is recorded with the acknowledging person and timestamp.

### `CommunityMagistrateSettings`
One per community, created by admin (or seeded by `ingress_magistrate`). Controls:
- `max_fine_pct` — maximum infraction fine as a percentage of assessed holdings (default 10%)
- `fine_collection_account` — `LedgerAccount` that receives collected fines

**Both must be set before fines create real obligations.** If `fine_collection_account` is not set, fines are recorded in the decision ledger but have no financial effect.

### `MagistrateFine`
Created alongside a `MagistrateDecision` (type `infraction_fine`) in a single atomic transaction. Records:
- `target_person`, `target_account`, `asset`
- `authority_domain` — which of the magistrate's oversight domains justified the fine
- `fine_pct`, `basis_amount_display`, `fine_amount_display`
- `infraction` — required public description of the infraction
- `obligation` FK → `assets.Obligation` (the actual financial enforcement)
- Status: `issued` / `obligation_created` / `pending_collection` / `contested` / `overturned`

---

## Decision flow

### Issuing a decision (non-fine)

1. Magistrate opens Power Console (`/magistrate/<pk>/console/`)
2. Clicks a domain action button → Alpine slide-down form opens
3. Submits title + body → `make_decision` POST
4. `MagistrateDecision` created immediately with `status = "active"`
5. Decision is visible in the assembly's community view
6. Any community member (except the magistrate) can call `review_decision` → `status = "reviewed"`

### Issuing an infraction fine

1. Magistrate opens the **Infraction Fines** section (only shown if `role.can_set_fines`)
2. Selects: authority domain, target member, asset, fine percentage (≤ community max), infraction description
3. `issue_fine` POST:
   - Validates magistrate is active and the target is a community member
   - Validates `fine_pct ≤ CommunityMagistrateSettings.max_fine_pct`
   - Finds target's largest holding of the selected asset to determine `basis_amount`
   - Creates `MagistrateDecision` (type `infraction_fine`) — public record
   - If `fine_collection_account` is set: calls `create_obligation` (same service as tribunal) → 14-day deferred debt from target's account to collection account
   - Creates `MagistrateFine` linked to the decision
4. Fine appears in the magistrate's fines ledger on the console
5. Target can contest before the tribunal (changes `MagistrateFine.status` to `contested`)

### Trade reversal

Trade reversal (`overseeing_trade`) does not delete the original ledger entry. It issues a `trade_reversal` decision that orders a counter-trade with a mandatory fine on the offending party. The reversal and fine are contestable before the tribunal.

---

## Accountability mechanisms

| Mechanism | How |
|---|---|
| Decision ledger | Every decision is permanent and public. Revocation is itself recorded. |
| Assembly review | Any community member can mark a decision as reviewed. |
| Impeachment | Any community member (except the magistrate) can open an `IMPEACHMENT` proposal in the assembly. When it passes, `_enact_proposal` sets `magistrate.status = "impeached"` — no further decisions possible. |
| Fine obligation | Infraction fines create asset obligations due in 14 days. Settleable via `fulfill_obligation`. |
| Reports | Magistrates submit periodic reports; the assembly acknowledges them. Creates an auditable record. |

The console banner states explicitly: *"Every decision is recorded in the public decision ledger and is subject to assembly review."*

---

## Power Console access

`/magistrate/<pk>/console/` raises `PermissionDenied` if the logged-in user's `Person` is not the magistrate's `person`. Only the seat-holder can access their own console.

The profile plugin (`magistrate/plugins/profile_plugins.py`) adds a "Command Console" link to the person's own profile page when they hold at least one active magistrate seat.

---

## Assembly integration

- **Election**: `AssemblyProposalType.MAGISTRATE_ELECTION` — nominee + role + term in metadata. On pass, `elect_confirm` creates the `Magistrate`.
- **Impeachment**: `AssemblyProposalType.IMPEACHMENT` — magistrate pk in metadata. On pass, `_enact_proposal` sets status to `impeached`.
- **Decision review**: Active decisions are shown in the community assembly view. Members click "Review" to mark them acknowledged.
- **Proposal filter**: Election and impeachment proposals are excluded from the normal proposal creation form — they have dedicated entry points.

---

## Configuration checklist

Before the magistrate system is fully operational in a community:

1. Run `ingress_magistrate` — seeds roles and creates `CommunityMagistrateSettings` with 10% max fine and treasury as collection account.
2. Elect at least one magistrate via the community assembly.
3. Optionally: adjust `max_fine_pct` and `fine_collection_account` per community in Django admin.

---

## Ingress

`management/commands/ingress_magistrate.py` seeds:
- All 10 `MagistrateRole` records (idempotent via `update_or_create`)
- Demo magistrate seats for the first 3 communities (random federal agents)
- The admin user's `Person` as Prefect in the first community (`_seed_founder_prefect`)
- `CommunityMagistrateSettings` for the first 3 communities, collection account = treasury (`_seed_community_settings`)
