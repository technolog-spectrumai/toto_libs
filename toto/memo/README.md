# toto.memo

Flashcard and diagram system. Supports spaced-repetition deck creation and inline diagram (Mermaid/Graphviz) storage. Decks are embedded in `academy` lessons.

## Purpose

Teachers build `MemoDeck` collections of `MemoCard` flashcards — each card has a front (prompt) and back (answer) plus optional difficulty rating and embedded diagram. Diagrams are stored as `MemoDiagram` records with raw source (Mermaid or Graphviz syntax) and cached SVG output. A `Lesson` in the academy is backed by a `MemoDeck` as its lecture content. Members can also create decks independently for personal study.

## Models

- `Tag` — simple tag model for memo decks. Fields: `name`, `slug`.

- `MemoDiagram` — a named diagram. Fields: `title`, `diagram_type` (`mermaid / graphviz / svg`), `source` (raw diagram source code), `rendered_svg` (cached SVG output), `author` (FK to `people.Person`), `created_at`.

- `MemoDeck` — a flashcard deck. Fields: `title`, `description`, `tags` (M2M), `author` (FK to `people.Person`), `is_public`, `community` (FK to `socialhub.Community`, nullable), `created_at`.

- `MemoCard` — a single flashcard within a deck. Fields: `deck` FK, `front` (question / prompt), `back` (answer / explanation), `order`, `difficulty` (`easy / medium / hard`), `diagram` (FK to `MemoDiagram`, nullable), `hint`, `tags` (M2M).

## Key coupling

- `academy.Lesson.memo_deck` — lessons of type `deck` embed a `MemoDeck` directly in the course module.
- `MemoDiagram` can be embedded in `MemoCard.diagram` to show visual content on the card back.

## Dependencies

- `vault` — MemoDiagram rendered SVG optionally stored as VaultFile
