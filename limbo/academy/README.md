# toto.academy

Learning management system (LMS). Courses, modules, lessons, student enrollment, certificates, cohorts, and learning paths.

## Purpose

A `Teacher` creates a `Course` structured into `CourseModule` → `Lesson` chains. Each lesson is backed by a `MemoDeck` of flashcards. A module can have an exam `Quiz`; passing unlocks a `SkillBadge`. Students enroll, work through lessons, take quizzes, and earn certificates. `Cohort` records let a teacher run a group through a course on a shared schedule. `LearningPath` sequences badges into progressions for structured skill development.

## Models

- `Teacher` — a `Person` authorized to create courses. Fields: `person` (OneToOne FK), `title`, `bio`, `is_active`.

- `Course` — a structured learning program. Fields: `title`, `slug`, `description`, `owner` (FK to `Teacher`), `is_published`, `is_virtual`, `order`.

- `CourseModule` — a chapter within a course. Fields: `course` (FK), `title`, `slug`, `description`, `order`, `owner` (FK to `Teacher`), `unlocks_badge` (FK to `competence.SkillBadge`), `verbena_page` (FK to `palimpsest.Page`, nullable), `exam` (FK to `quizzes.Quiz`, nullable), `attached_quizzes` (M2M to `quizzes.Quiz`).

- `Lesson` — a single learning unit within a module. Fields: `module` (FK), `title`, `slug`, `summary`, `order`, `owner` (FK to `Teacher`), `lecture` (FK to `memo.MemoDeck`), `video_file` (FK to `vault.VaultFile`, nullable), `attached_quizzes` (M2M to `quizzes.Quiz`).

- `Student` — a `Person` enrolled in the academy. Fields: `person` (OneToOne FK), `badges` (M2M through `StudentBadge`), `enrolled_courses` (M2M through `CourseEnrollment`).

- `StudentBadge` — a through model linking `Student` to `competence.SkillBadge`. Fields: `student`, `badge`, `awarded_at`.

- `CourseEnrollment` — enrollment through model. Fields: `student` (FK), `course` (FK), `enrolled_at`, `completed_at`.

- `Certificate` — issued on course completion. Fields: `uuid` (UUIDField), `person` (FK), `course` (FK, nullable), `exam` (FK to `quizzes.Quiz`, nullable), `title`, `description`, `granted_at`, `signed_by` (FK to `Teacher`), `signing_key` (FK to `gervazy.EncryptedPrivateKey`).

- `Cohort` — a time-bounded group running through a course together. Fields: `course` (FK), `teacher` (FK, nullable), `title`, `slug`, `starts_at`, `ends_at`, `capacity`, `is_active`.

- `CohortMembership` — links a `Student` to a `Cohort`. Fields: `cohort`, `student`, `joined_at`.

- `LearningPath` — a curated progression of skill badges. Fields: `title`, `slug`, `description`, `badges` (M2M through `LearningPathBadge`), `is_published`, `order`.

- `LearningPathBadge` — ordered badge step in a learning path. Fields: `learning_path`, `badge` (FK to `competence.SkillBadge`), `order`, `note`.

- `Script` / `ScriptSection` — extends `verbena.AbstractPage` / `AbstractSection`. Long-form instructional content attached to a course module.

## Key coupling

- `memo.MemoDeck` — lessons of type `deck` embed a flashcard deck.
- `vault.VaultFile` — certificates are stored as vault files.
- `assets.LedgerAccount` — paid courses debit the buyer's account.

## Dependencies

- `competence` — SkillBadge unlocked on module completion
- `memo` — MemoDeck embedded in deck-type lessons
- `palimpsest` — Page attached as course notes/textbook
- `quizzes` — Quiz attached to modules; Certificate links to quiz result
- `vault` — Certificate PDFs stored as VaultFile
- `verbena` — Script/ScriptSection extend AbstractPage/AbstractSection
