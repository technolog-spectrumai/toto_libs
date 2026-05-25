# toto.verbena

Abstract page/section/tag system. Provides three base models inherited by every content app in the system. Verbena itself has no concrete models and no URL routes.

## Models (abstract bases)

- `AbstractTag` — extends `DomainEntity`. A tagging primitive. Concrete subclasses: `palimpsest.Tag`, `socialhub.CommunityNewsTopic`, `memo.Tag`, `library.*Tag`.

- `AbstractPage` — extends `DomainEntity`. A titled, slugged, rich-text document. Fields: `title`, `slug`, `description` (intro/summary), `cover_image`, `is_published`, `published_at`, `created_at`, `updated_at`. Concrete subclasses: `palimpsest.Page`, `kanban.DocumentationPage`, `academy.Script`.

- `AbstractSection` — extends `DomainEntity`. A content block within a page. Fields: `page` (generic FK via the concrete subclass), `title`, `order`, `content` (HTML/markdown body), `author` (FK to `people.Person`, nullable), `created_at`. Concrete subclasses: `palimpsest.Section`, `kanban.DocumentationSection`, `academy.ScriptSection`, `socialhub.CommunityNewsPost`.

## Key coupling

Every content app inherits from these bases. Changes to verbena abstract fields propagate to all concrete subclasses via Django's multi-table inheritance.
