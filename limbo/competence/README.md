# toto.competence

Skills registry. Defines skill groups and named skill badges that practitioners and responders can earn. Supports skill prerequisites.

## Purpose

`SkillBadge` records are the credential unit of the platform. `academy` awards them when students pass module exams. `mobilization` links them to `ResponderSkill` records to track certified responder proficiencies. `kanban` tasks can declare required skills in metadata. `SkillBadgePrerequisite` enforces learning order — you can't earn "Advanced First Aid" without "Basic First Aid" first.

## Models

- `Experience` — a named experience/qualification type. Fields: `name`, `slug`, `description`. Reference data.

- `SkillGroup` — a category of skills. Fields: `name`, `slug`, `description`, `icon`. Examples: `mobilization`, `technical`, `leadership`.

- `SkillBadge` — a single skill within a group. Fields: `group` FK, `name`, `slug`, `description`, `icon`, `level` (`basic / intermediate / advanced / expert`), `is_active`.

- `SkillBadgePrerequisite` — a directed prerequisite link between two badges. Fields: `badge` FK, `prerequisite` FK (to `SkillBadge`). Unique on `(badge, prerequisite)`. Prevents self-referential prerequisites via `clean()`.

## Key coupling

- `mobilization.ResponderSkill` — each responder skill links to a `SkillBadge` with a verified proficiency level.
- `kanban.Task` metadata — tasks can specify required skills (via `skill_metadata()` in `detections.services`).
- Skills are seeded per-group by ingress commands (e.g. `ingress_mobilization` seeds 7 mobilization skills).

## Dependencies

- `people` — Experience and SkillBadge are awarded to Person records
