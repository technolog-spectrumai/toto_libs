# toto.bento

Idea management and innovation pipeline. Organizes ideas into categorized boxes with directed links between them, supporting concept mapping and ideation workflows.

## Purpose

Community members capture ideas as `IdeaBox` records and draw directional relationships between them (`builds_on`, `contradicts`, `leads_to`). Ideas move through a status pipeline from raw idea → exploring → validated → implementing. The resulting graph of linked ideas forms a visual concept map that the community can use to prioritize and track innovation initiatives.

## Models

- `Category` — extends `DomainEntity`. A classification for idea boxes. Fields: `name`, `slug`, `community` (FK), `color`, `icon`.

- `IdeaBox` — extends `DomainEntity`. A named idea or concept. Fields: `title`, `description`, `category` (FK), `community` (FK), `author` (FK to `people.Person`), `status` (`idea / exploring / validated / implementing / archived`), `is_public`, `tags` (M2M), `score` (computed from links/votes).

- `IdeaLink` — a directed relationship between two boxes. Fields: `from_box` (FK to `IdeaBox`), `to_box` (FK to `IdeaBox`), `link_type` (`builds_on / contradicts / related / leads_to`), `description`, `author` (FK to `people.Person`). Unique on `(from_box, to_box, link_type)`.

## Key coupling

- Community-scoped. Standalone from financial/governance systems.

## Dependencies

- `people` — IdeaBox.author and IdeaLink.author
- `socialhub` — Community-scoped categories and idea boxes
