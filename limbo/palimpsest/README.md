# toto.palimpsest

Collaborative long-form writing. Multi-author documents composed of ordered sections. Authors are inferred from section authorship — no single owner.

## Purpose

Any member can create a `Page` and add `Section` blocks authored by different people. There is no single page owner — authorship is distributed across sections. Pages accumulate `word_count` and `reading_time_minutes` properties from their sections automatically. This makes palimpsest suitable for community manifests, field notes, and collaborative essays where multiple voices contribute to a single document.

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

## Dependencies

- `people` — Section.author FK to Person
- `verbena` — Page extends AbstractPage; Section extends AbstractSection
