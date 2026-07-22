# toto.socialhub

Community identity and membership management. A `Community` is the primary grouping unit: it owns an economy, a governance system, a marketplace, and a responder pool.

## Purpose

A `Community` is the container around which everything else organizes. People join via `MembershipApplication` (email-verified, requires a reference from an existing member via `ReferenceRequest`). Once a member, they can participate in governance, trade in the shop, work on projects, and respond to emergencies. The `is_federal_tribe` flag elevates a community to federal status — its members become eligible as emergency responders. Communities form hierarchies (parent → child) and share a `Federation`.

## Models

- `Community` — extends `DomainEntity`. Key fields:
  - `federation` — FK to `core.Federation`
  - `org_type` — `guild / company / non_profit / family / other`
  - `is_autonomous` — bool; self-governing community with internal leadership
  - `is_federal_tribe` — bool; members are eligible as emergency responders and exempt from poll tax
  - `is_foreign` — bool; community outside local jurisdiction
  - `head` — FK to `people.Person` (community leader)
  - `senior_members` — M2M to `people.Person` (can manage news/announcements)
  - `location` — FK to `locations.Address`
  - `territory` — FK to `locations.Territory`
  - `parent` — FK to self (community hierarchy)
  - `email_service` — FK to `api.EmailService`

- `CommunityNewsTopic` — extends `AbstractTag`. Tag/category for news posts.

- `CommunityNewsPost` — extends `AbstractSection`. A rich-text news article inside a community. Fields: `community` (FK), `author` (FK to `people.Person`), `topics` (M2M to `CommunityNewsTopic`), `visibility` (`public / community`).

- `MembershipApplication` — an email-verified request to join a community. Fields: `email`, `community` (FK), `code` (6-digit verification), `verified_at`, `expires_at`.

- `ReferenceRequest` — a reference letter provided by an existing member for a `MembershipApplication`. Fields: `application` (FK to `MembershipApplication`), `referrer` (FK to `people.Person`), `message`, `status` (`pending / accepted / declined`), `responded_at`.

- `Constitution` — community founding document. Fields: `community` (FK), `content`, `version`, `adopted_at`.

- `ConstitutionSignature` — a person's signature on a constitution. Fields: `constitution` (FK), `person` (FK to `people.Person`), `signed_at`.

## Key coupling

- `Community` is referenced by nearly every domain model (kanban projects, bazaar shops, assembly proposals, mobilization events, emergency statuses, deployments).
- `Community.is_federal_tribe` gates `mobilization.Responder` eligibility.

## Dependencies

- `api` — EmailService FK for community notification emails
- `locations` — Community headquarters Address FK and Territory FK
- `people` — CommunityNewsPost author; MembershipApplication applicant

## Enigma JSON API

| Method | URL | Description |
|--------|-----|-------------|
| GET | `/socialhub/api/profiles/` | List people profiles (sorted by display_name) |
| GET | `/socialhub/api/profiles/{slug}/` | Profile detail with communities list |
| GET | `/socialhub/api/communities/` | List communities (sorted by name) |
| GET | `/socialhub/api/communities/{slug}/` | Community detail with senior members + latest news |

All endpoints are public (no auth required).

### Testing
```bash
cd portal && python manage.py test toto.socialhub.tests_api
```
