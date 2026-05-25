# toto.library

Media and reference library. Stores books, articles, audio references, and video references. Items can be organized into collections and attached to vault files.

## Models

- `LibraryItem` — abstract base (extends `DomainEntity`). Common fields: `title`, `description`, `tags` (M2M to `memo.Tag`), `vault_file` (FK to `vault.VaultFile`, nullable), `community` (FK), `is_public`, `author_name`, `published_at`.

- `Book` — extends `LibraryItem`. Adds: `isbn`, `publisher`, `edition`, `page_count`, `language`.

- `Article` — extends `LibraryItem`. Adds: `journal`, `doi`, `url`, `abstract`.

- `AudioReference` — extends `LibraryItem`. Adds: `duration_seconds`, `format` (`mp3 / wav / ogg / flac`), `url`.

- `VideoReference` — extends `LibraryItem`. Adds: `duration_seconds`, `platform` (`youtube / vimeo / internal / other`), `url`, `embed_code`.

- `LibraryCollection` — a named grouping of items. Fields: `name`, `description`, `community` FK, `books` / `articles` / `audio` / `videos` (M2M to each type), `curator` (FK to `people.Person`), `is_public`.

## Key coupling

- `vault.VaultFile` — library items can attach their source file for download.
- `memo.Tag` — shared tagging system with the memo/flashcard app.
