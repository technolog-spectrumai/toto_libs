# toto.people

Person profiles. The `Person` model is the shared identity anchor across every domain app. Every user who appears in a community, on a project, in the ledger, or in an emergency response first exists as a `Person`.

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
