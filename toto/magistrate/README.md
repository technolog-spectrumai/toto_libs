# toto.magistrate

Community magistracy — a local enforcement and advisory layer between the assembly and the tribunal. Magistrates are appointed via assembly proposals and can issue decisions and fines.

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
