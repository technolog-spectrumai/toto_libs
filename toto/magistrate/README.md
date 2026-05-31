# toto.magistrate

Community magistracy — a local enforcement and advisory layer between the assembly and the tribunal. Magistrates are appointed via assembly proposals and can issue decisions and fines.

## Purpose

A community votes (via `assembly`) to appoint a `Magistrate` to a named role. The magistrate can then issue `MagistrateDecision` records (advisories, directives, injunctions) and levy `MagistrateFine` penalties that create `assets.Obligation` records against the target's ledger account. Misconduct can be reported via `MagistrateReport`. `CommunityMagistrateSettings` configures the fine collection account and term limits. Magistracy sits between the legislative assembly and the judicial tribunal — it handles routine enforcement without needing a full jury.

## Models

- `MagistrateRole` — named role type (e.g. "Prefect", "Tribune", "Legate"). Fields: `name`, `slug`, `description`, `icon`, oversight domain booleans (`overseeing_tribunal`, `overseeing_trade`, `overseeing_finance`, `overseeing_public_order`, `overseeing_merchandise`, `overseeing_education`, `overseeing_relations`, `overseeing_logistics`, `overseeing_interior`, `overseeing_productivity`), `can_set_fines`, `can_freeze_assets`, `order`.

- `Magistrate` — a `Person` appointed to a magistrate role in a community. Fields: `person` (FK), `role` (FK to `MagistrateRole`), `community` (FK), `status` (`active / suspended / impeached / term_ended`), `term_start`, `term_end`, `elected_at`, `source_proposal` (FK to `assembly.AssemblyProposal` — the proposal that elected them).

- `MagistrateDecision` — a formal decision issued by a magistrate. Fields: `magistrate` FK, `community` FK, `title`, `body`, `decision_type` (one of ~15 types: `emergency_declare`, `tribunal_order`, `infraction_fine`, `asset_freeze`, etc.), `status` (`active / revoked / reviewed`), `reviewed_by` (FK to `people.Person`).

- `MagistrateReport` — a periodic report submitted by a magistrate to the assembly. Fields: `magistrate` FK, `title`, `body`, `status` (`draft / submitted / acknowledged`), `acknowledged_by` (FK to `people.Person`), `acknowledged_at`.

- `CommunityMagistrateSettings` — per-community magistracy config. Fields: `community` (OneToOne), `max_fine_pct` (Decimal 0–100), `fine_collection_account` (FK to `assets.LedgerAccount`).

- `MagistrateFine` — an infraction fine levied by a magistrate under a decision. Fields: `decision` (OneToOne), `target_person` (FK), `target_account` (FK to `assets.LedgerAccount`), `asset` (FK), `authority_domain`, `fine_pct`, `fine_amount_display`, `infraction`, `status` (`issued / obligation_created / pending_collection / contested / overturned`), `obligation` (FK to `assets.Obligation`).

- `AssetFreeze` — a magistrate-issued freeze on an asset token. Fields: `asset` (FK), `decision` (OneToOne), `reason`, `status` (`active / lifted`), `lifted_at`, `lift_decision` (FK to `MagistrateDecision`).

## Key coupling

- `assembly.AssemblyProposal` — magistrates are appointed via assembly vote.
- `assets.Obligation` — fines create obligations against the target account.
- `assets.LedgerAccount` — fine collection account defined in settings.
- `tribunal.TribunalCase` — decisions can reference tribunal cases.

## Dependencies

- `assembly` — MagistrateDecision can reference an AssemblyDecision
- `assets` — MagistrateFine denominated in Asset
- `people` — Magistrate and all parties are Person records
- `socialhub` — CommunityMagistrateSettings is community-scoped
