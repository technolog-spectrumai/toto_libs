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

## Dependencies

- `assets` — CommunityTransactionFee references LedgerAccount; PollTax uses Asset
- `people` — AssemblyProposal.proposer, AssemblyVote.voter, SenateVeto.vetoed_by
- `socialhub` — Every proposal and config is community-scoped
