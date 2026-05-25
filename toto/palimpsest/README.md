# toto.palimpsest

Collaborative long-form writing. Multi-author documents composed of ordered sections. Authors are inferred from section authorship — no single owner.

## Models

- `Tag` — extends `verbena.AbstractTag`. Tagging for palimpsest pages.

- `Page` — extends `verbena.AbstractPage`. A collaborative document. Tags M2M to `Tag`. Key properties:
  - `authors()` — returns `People.Person` queryset: all persons who authored at least one section
  - `word_count` — summed across all sections + description
  - `reading_time_minutes` — `max(1, word_count / 220)`
  - `excerpt` — first section's content if no description

- `Section` — extends `verbena.AbstractSection`. One authored block within a page. Fields: `page` (FK to `Page`), `title`, `order`, `content` (rich text), `author` (FK to `people.Person`).

## Key coupling

- `verbena.AbstractPage` / `AbstractSection` — concrete implementations.
- Authors are `people.Person` records; no community scoping (palimpsest pages are platform-wide by default).
