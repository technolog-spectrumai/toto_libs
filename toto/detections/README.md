# toto.detections

Incident and threat detection registry. Records detected events — hazards, incidents, threats — with geographic location, severity, and status. Feeds the mobilization pipeline and the kanban task system.

## Purpose

A community member (or an automated sensor feed) files a `Detection`. The detection is geo-tagged, categorized, and rated for severity. A `DetectionHandle` assigns a responder to work it. If the detection is serious enough, it is linked as evidence to a `MobilizationReport`, whose severity is recalculated from the weighted scores of all attached detections. Once a mobilization event is activated, field `Intervention` records link back to the detection they are mitigating — creating a full chain: detection → report → event → deployment → intervention.

## Models

- `DetectionCategory` — extends `DomainEntity`. Hierarchical category tree (self-referential `parent` FK). Examples: Natural Disaster > Flood, Security > Intrusion.

- `Detection` — extends `EventBase` (which extends `DomainEntity`). The core record. Key fields:
  - `category` — FK to `DetectionCategory`
  - `address`, `zone`, `route` — FKs to `locations.*` (geographic anchors)
  - `reported_by` — FK to `people.Person`
  - `involved_persons` — M2M to `people.Person`
  - `mitigation_task` — FK to `kanban.Task` (the task designated to resolve this detection)
  - `severity` — `low / medium / high / critical`
  - `detection_type` — `incident / hazard / threat / observation`
  - `status` — `new / acknowledged / in_progress / mitigated / closed / false_positive`
  - `severity_score` (float, calculated from weighted evidence) — used by `mobilization` for report severity

- `DetectionHandle` — extends `DomainEntity`. An assignment of a person to work a detection. Fields: `detection` FK, `assigned_to` (FK to `people.Person`), `status` (`open / in_progress / resolved / transferred`), `notes`, `opened_at`, `closed_at`.

## Services (`services.py`)

| Function | What it does |
|---|---|
| `create_detection_help_task(detection, owner, reviewer)` | Creates a `kanban.Task` and links it as `detection.mitigation_task` |
| `ensure_detection_mitigation_task(detection, ...)` | Idempotent: creates the task only if not already linked |
| `detection_map_feature(detection)` | Returns a GeoJSON feature dict for map rendering |
| `skill_metadata(required_skills)` | Builds skill metadata dict for task creation |

## Key coupling

- `mobilization.MobilizationReportEvidence` — detections are attached as evidence to mobilization reports. Severity scores flow upward to recalculate report severity.
- `response.Intervention.detection` — an intervention tracks which detection it mitigates.
- `kanban.Task` — `mitigation_task` FK creates a direct link between a detection and its project-management resolution.
- `locations` — geographic anchors for map display.

## Dependencies

- `events` — Detection can link to a ScheduledEvent
- `people` — Detection reporter and handler are Person records
