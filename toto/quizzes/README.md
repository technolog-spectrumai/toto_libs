# toto.quizzes

Personality and assessment quiz engine. Quizzes produce trait scores rather than right/wrong grades, making them suitable for self-assessment, role matching, and onboarding surveys.

## Models

- `Quiz` — a named quiz. Fields: `title`, `description`, `community` (FK), `is_active`, `is_public`, `created_by` (FK to `people.Person`).

- `QuizQuestion` — a question within a quiz. Fields: `quiz`, `text`, `order`, `is_required`.

- `QuizAnswer` — one answer option for a question. Fields: `question`, `text`, `order`.

- `QuizAnswerTrait` — links an answer to a trait with a score weight. Fields: `answer`, `trait` (FK to `QuizTrait`), `weight` (float). Multiple traits can be affected by a single answer.

- `QuizTrait` — a named dimension being measured. Fields: `quiz`, `name`, `slug`, `description`. Example: `leadership`, `technical_aptitude`, `empathy`.

- `QuizAttempt` — one person's completed quiz attempt. Fields: `quiz`, `person` (FK to `people.Person`), `started_at`, `completed_at`, `is_complete`, `result_metadata` (JSON — computed trait scores).

- `QuizAttemptAnswer` — the answer selected in one attempt. Fields: `attempt`, `question`, `selected_answer`.

## Key coupling

- Standalone; no hard FK dependencies on other domain apps.
- Results (`result_metadata`) are stored as JSON on the attempt and can be read by `academy` enrollment flows or community onboarding.

## Dependencies

- `people` — QuizAttempt.participant FK to Person
