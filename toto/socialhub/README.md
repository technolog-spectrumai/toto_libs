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
