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
  - `digital_signature` — TextField; base64-encoded PNG of the person's handwritten (canvas) signature, used as a decorative element in signed documents
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
- `gervazy.PersonSigningKey` — the person's active Ed25519 signing key (stored encrypted in their strongbox)
- `contracts.ContractSignatory` — records of contracts this person has been asked to sign

`is_federal_agent` and community membership (via `communities` M2M) are read by `mobilization.Responder.clean()` to enforce eligibility.

`digital_signature` stores a decorative handwritten signature (base64 PNG), separate from the cryptographic Ed25519 key managed by gervazy.

## Dependencies

- `locations` — Person.address FK to locations.Address
