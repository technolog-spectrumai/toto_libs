# toto.academy

Learning management system (LMS). Courses, modules, lessons, student enrollment, certificates, cohorts, and learning paths.

## Models

- `Teacher` — a `Person` authorized to create courses. Fields: `person` (FK), `community` (FK), `is_active`, `bio`.

- `Course` — a structured learning program. Fields: `teacher` FK, `community` FK, `title`, `description`, `cover_image`, `status` (`draft / published / archived`), `is_public`, `price_base_units`, `price_asset`, `max_students`, `tags` (M2M).

- `CourseModule` — a chapter within a course. Fields: `course`, `title`, `description`, `order`, `is_published`.

- `Lesson` — a single learning unit within a module. Fields: `module`, `title`, `content` (rich text), `lesson_type` (`text / video / audio / quiz / deck`), `memo_deck` (FK to `memo.MemoDeck`, nullable — for flashcard lessons), `order`, `duration_minutes`.

- `Student` — a `Person` enrolled in the academy. Fields: `person` (OneToOne), `community` FK, `is_active`.

- `StudentBadge` — an achievement badge awarded to a student. Fields: `student`, `badge_type` (slug), `awarded_at`, `metadata`.

- `CourseEnrollment` — enrollment record. Fields: `student`, `course`, `status` (`enrolled / completed / dropped`), `enrolled_at`, `completed_at`, `progress_percent`.

- `Certificate` — issued on course completion. Fields: `enrollment` (OneToOne), `issued_at`, `certificate_number` (unique UUID slug), `vault_file` (FK to `vault.VaultFile`, nullable — PDF).

- `Cohort` — a time-bounded group running through a course together. Fields: `course`, `name`, `starts_at`, `ends_at`, `max_size`, `is_active`.

- `CohortMembership` — links a `Student` to a `Cohort`.

- `LearningPath` — a curated sequence of courses. Fields: `community`, `title`, `description`, `courses` (ordered M2M to `Course`).

- `LearningPathBadge` — badge awarded on path completion.

- `Script` / `ScriptSection` — extends `verbena.AbstractPage` / `AbstractSection`. A long-form narrative document attached to a course module (course notes, textbooks).

## Key coupling

- `memo.MemoDeck` — lessons of type `deck` embed a flashcard deck.
- `vault.VaultFile` — certificates are stored as vault files.
- `assets.LedgerAccount` — paid courses debit the buyer's account.
