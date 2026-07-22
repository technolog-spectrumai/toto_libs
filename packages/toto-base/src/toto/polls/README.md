# toto.polls

Lightweight ad-hoc polling. Simple polls with options and votes. Separate from the `assembly` governance system — intended for informal community temperature checks, not binding decisions.

## Purpose

A community member creates a `Poll` with a question and 2–N `Option` choices. Members vote; results are tabulated in real time. Polls close at `closes_at`. Unlike `assembly` proposals, polls produce no `AssemblyDecision` and have no governance effect — they are purely informational. Use `assembly` for binding votes on rules, fees, or taxes.

## Models

- `Poll` — a question put to a community or group. Fields: `community` (FK to `socialhub.Community`), `author` (FK to `people.Person`), `title`, `question`, `is_multiple_choice`, `status` (`open / closed`), `closes_at`, `is_public`, `created_at`.

- `Option` — one choice within a poll. Fields: `poll` FK, `text`, `order`.

- `Vote` — a person's selection. Fields: `poll`, `option`, `voter` (FK to `people.Person`), `cast_at`. Unique on `(poll, voter, option)` when multiple choice; unique on `(poll, voter)` otherwise (enforced at service layer).

## Key coupling

- Polls are community-scoped but otherwise standalone.
- For binding governance votes, use `assembly.AssemblyProposal` / `AssemblyVote`.

## Dependencies

None — standalone app.
