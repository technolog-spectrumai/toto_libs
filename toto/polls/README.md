# toto.polls

Lightweight ad-hoc polling. Simple polls with options and votes. Separate from the `assembly` governance system — intended for informal community temperature checks, not binding decisions.

## Models

- `Poll` — a question put to a community or group. Fields: `community` (FK to `socialhub.Community`), `author` (FK to `people.Person`), `title`, `question`, `is_multiple_choice`, `status` (`open / closed`), `closes_at`, `is_public`, `created_at`.

- `Option` — one choice within a poll. Fields: `poll` FK, `text`, `order`.

- `Vote` — a person's selection. Fields: `poll`, `option`, `voter` (FK to `people.Person`), `cast_at`. Unique on `(poll, voter, option)` when multiple choice; unique on `(poll, voter)` otherwise (enforced at service layer).

## Key coupling

- Polls are community-scoped but otherwise standalone.
- For binding governance votes, use `assembly.AssemblyProposal` / `AssemblyVote`.
