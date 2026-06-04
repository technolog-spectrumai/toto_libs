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

## Dependencies

- `assets` — TribunalRuling compensation denominated in Asset
- `bazaar` — TribunalCase can reference a disputed bazaar Order
- `inventory` — TribunalClaim can reference a RealWorldObject
- `people` — TribunalParty and JuryVote.juror are Person records
