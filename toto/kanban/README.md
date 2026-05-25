# toto.kanban

Project management with ledger-backed compensation. Projects contain missions → sprints → tasks, with practitioner roles and allowance-based income tied to `assets.LedgerAccount`.

## Purpose

A community creates a `Project`, adds `Practitioner` members with roles, and breaks work into `Campaign` → `Mission` → `Task` chains across `Sprint` iterations. Practitioners earn compensation through `PractitionerAllowance` records — daily, hourly, or fixed amounts paid from a ledger account — automatically posted by Celery beat at 17:00 on weekdays. Projects can be tokenized on the ledger as shares. Emergency response operations overlay kanban campaigns and missions for field command structure.

## Models

- `Project` — extends `DomainEntity`. Top-level container. FK to `socialhub.Community`. Has `is_public`, `is_archived`.
- `Column` — a kanban board state column inside a project (`Backlog`, `In Progress`, `Done`, etc.). Fields: `project`, `name`, `order`, `is_done_column`, `auditors` (M2M to `Practitioner` — who may move tasks here).
- `Campaign` — a named initiative within a project. FK to `Project`. Used as an overlay by `mobilization.MobilizationEvent`.
- `Mission` — a goal within a campaign. Fields: `campaign`, `title`, `is_complete`. Deployments in the `response` app can optionally link to a `Mission`.
- `Sprint` — a time-boxed iteration. Fields: `project`, `name`, `starts_at`, `ends_at`, `is_active`.
- `Practitioner` — a `Person`'s role in a project. Fields: `project`, `person` (FK to `people.Person`), `role` (`contributor / reviewer / auditor / manager / observer`), `default_income_account` (FK to `assets.LedgerAccount`), `joined_at`.
- `ProjectCommitment` — practitioner commitment record. Fields: `practitioner`, `hours_per_day`, `starts_at`, `ends_at`.
- `Task` — a unit of work. Fields: `column`, `sprint`, `assigned_to` (FK to `Practitioner`), `reviewer`, `title`, `description`, `weight` (Fibonacci: 1/2/3/5/8), `completed_at`. M2M to `Practitioner` (collaborators).
- `PractitionerAllowance` — recurring or fixed compensation rule. Fields: `practitioner`, `payer_account` (FK to `assets.LedgerAccount`), `recipient_account`, `allowance_type` (`per_diem / hourly / fixed / travel / meal / other`), `amount_base_units`, `asset`, `is_active`, `starts_at`, `ends_at`.
- `DocumentationPage` / `DocumentationSection` — rich documentation on a mission. `DocumentationPage` can be marked `is_manual`.
- `ProjectTokenization` — links a `Project` one-to-one to an `assets.Asset`. Immutable; delete is blocked. Represents project equity on the ledger.

## Celery beat task

`toto.kanban.tasks.pay_daily_allowances` runs weekdays at 17:00. For every active `PractitionerAllowance`, it creates an `assets.Obligation` against the payer account.

## Key coupling

- `assets.LedgerAccount` — income accounts for practitioners, payer accounts for allowances.
- `mobilization.MobilizationEvent.kanban_campaign` — events overlay a campaign board.
- `response.Deployment.kanban_mission` — deployments can be linked to a mission board.
- `detections.Detection.mitigation_task` — a task can be the designated mitigation for a detection.
