# Mobilization App

Strategic command layer for emergency response. Handles the full upstream pipeline from detection to enacted event, including responder registry, emergency declarations, and campaign management. Field execution lives in the `response` app.

---

## How it works

```
Detection (detections app)
    │
    ▼
MobilizationReport  ──── evidence ────► MobilizationReportEvidence
    │  (draft → submitted → reviewed → enacted)
    │
    ▼
MobilizationEvent  ──── optional links ──► kanban.Campaign, events.ScheduledEvent
    │
    ├──► EmergencyStatus  ──► EmergencyEquipmentAccess
    │         (watch / warning / emergency / critical_emergency)
    │         requires AssemblyProposal vote before activation
    │
    └──► response.Deployment  (FK boundary — field operations continue in response app)
```

---

## Internal model relationships

### Reference / lookup models

| Model | Used by |
|---|---|
| `IncidentType` | `MobilizationReport.incident_type`, `MobilizationEvent.incident_type` |
| `AchievementBadge` | `PersonAchievement.badge` |

### Responder registry

```
people.Person ──OneToOne──► Responder ──M2M──► socialhub.Community
                                │
                                ├──FK (one-to-many)──► ResponderSkill ──FK──► competence.SkillBadge
                                ├──reverse FK──────────► response.DeploymentAssignment
                                └──reverse FK──────────► response.Intervention (assigned_to)
```

`Responder` wraps a `Person` and tracks operational state (`current_status`). Status transitions are managed exclusively through `services.py` — never set directly. Transitions are triggered by field-side operations in `response` (via `services.activate_deployment_assignment`, `services.release_responder_from_deployment`, `services.complete_deployment`).

**Eligibility constraint** — `Responder.clean()` enforces that a person must be either `is_federal_agent=True` or a member of at least one `Community` with `is_federal_tribe=True`.

`PersonAchievement` awards an `AchievementBadge` to a `Person`, optionally tied to a `response.Deployment`. The `unique_together` on `(person, badge)` means each badge can only be awarded once per person.

### Report pipeline

```
MobilizationReport
    ├──FK──► socialhub.Community
    ├──FK──► IncidentType
    ├──FK──► people.Person  [submitted_by, reviewed_by, enacted_by]
    └──reverse FK──► MobilizationReportEvidence (evidence_links)
                          │
                          └──FK──► detections.Detection
```

Severity on `MobilizationReport` is recalculated automatically by `update_report_severity_from_evidence()` whenever evidence is added or changed. The algorithm:

1. For each linked detection: `score += (detection.severity_score + role_bump) × weight_factor`
   - `evidence_role` bumps: `primary +1`, `contradictory -1`, others `0`
   - `weight` factors: `high=1.5`, `normal=1.0`, `low=0.5`
2. `avg = total_score / evidence_count`
3. Thresholds: `≥2.5 → critical`, `≥1.8 → high`, `≥0.8 → medium`, else `low`

`unique_together` on `(report, detection)` — each detection can only be linked once per report.

### Event and emergency

```
MobilizationEvent
    ├──FK──► socialhub.Community
    ├──FK──► MobilizationReport  (source_report — must share community)
    ├──FK──► events.ScheduledEvent
    ├──FK──► kanban.Campaign  (campaign board overlay)
    ├──FK──► people.Person  (coordinator)
    ├──reverse FK──► response.Deployment  (field operations)
    ├──reverse FK──► response.EvacuationRoute
    └──reverse FK──► EmergencyStatus
```

`MobilizationEvent.clean()` validates that `community` matches `source_report.community`.

```
EmergencyStatus
    ├──FK──► MobilizationEvent
    ├──FK──► socialhub.Community  (nullable — at least one of community/zone required)
    ├──FK──► locations.Zone  (nullable)
    ├──FK──► people.Person  (declared_by)
    ├──FK──► assembly.AssemblyProposal  (source_proposal — the vote that authorized this)
    ├──FK──► assets.Asset  (emergency_tax_asset)
    ├──FK──► assets.LedgerAccount  (emergency_tax_account)
    └──reverse FK──► EmergencyEquipmentAccess (equipment_accesses)
```

**Emergency declaration flow** — direct creation is blocked. Must go through assembly vote:

1. Coordinator posts to `emergency_status_propose` → creates `AssemblyProposal(type="emg_declare")`
2. Community votes via the `assembly` app
3. Once `proposal.status == "passed"`, coordinator clicks **Activate Emergency** → `EmergencyStatus` created with `source_proposal` set

`EmergencyEquipmentAccess` tracks an `inventory.RealWorldObject` granted hybrid access under an active emergency. It optionally points to a `response.Deployment` that is using the item. `is_hybrid=True` marks shared access — ownership stays with the community/zone.

---

## Cross-app dependencies

| App | Models used | Purpose |
|---|---|---|
| `people` | `Person` | Responders, coordinators, reviewers |
| `socialhub` | `Community` | Incident community, responder affiliations, `is_federal_tribe` eligibility gate |
| `locations` | `Zone` | Emergency zone targeting |
| `inventory` | `RealWorldObject` | Hybrid emergency equipment items |
| `assets` | `Asset`, `LedgerAccount` | Emergency tax denomination and collection account |
| `competence` | `SkillBadge` | Responder skill registry |
| `detections` | `Detection` | Report evidence |
| `kanban` | `Campaign` | Campaign board overlay on events |
| `events` | `ScheduledEvent` | Optional calendar link |
| `assembly` | `AssemblyProposal` | Emergency declaration authorization gate |
| **`response`** | `Deployment`, `EvacuationRoute` | FK targets for event-level field operations |

### Key coupling points

- **`socialhub.Community.is_federal_tribe`** and **`people.Person.is_federal_agent`**: These flags gate responder eligibility in `Responder.clean()`. Changes to them can silently break responder registration.
- **`assembly` is write-only from mobilization**: The mobilization app creates `AssemblyProposal` records and reads their `status`. It never modifies assembly data.
- **`response` app**: `mobilization` is upstream. `response.Deployment` FKs into `mobilization.MobilizationEvent`. Mobilization never imports from response except for FK strings (`"response.Deployment"`).

---

## Services (`services.py`)

All business logic lives here. Views never modify models directly.

| Function | What it does |
|---|---|
| `can_enact_report(person, report)` | `True` if person is community head or senior member |
| `create_report_from_detection(community, detection, submitted_by)` | Creates report, links detection as `primary`/`high` evidence |
| `add_detection_evidence(report, detection, ...)` | Adds or updates evidence link; triggers severity recalc |
| `update_report_severity_from_evidence(report)` | Weighted-average severity recalculation |
| `submit_report(report, submitted_by)` | `draft → submitted` |
| `review_report(report, reviewed_by)` | `submitted → reviewed` |
| `enact_report(report, enacted_by, create_event=False)` | `→ enacted`; optionally creates event |
| `reject_report(report, rejected_by, notes)` | `submitted/reviewed → rejected` |
| `create_event_from_report(report, coordinator, ...)` | Creates `MobilizationEvent` from enacted report |
| `create_deployment(event, community, coordinator, ...)` | Creates `response.Deployment`; validates community match |
| `assign_responder_to_deployment(deployment, responder, ...)` | Creates `response.DeploymentAssignment`; raises if duplicate |
| `activate_deployment_assignment(assignment)` | `→ active`; responder `→ responding` |
| `release_responder_from_deployment(assignment)` | `→ released`; responder `→ available` if no other active |
| `create_intervention(deployment, **kwargs)` | Creates `response.Intervention` |
| `complete_intervention(intervention, outcome_notes)` | `→ done` |
| `complete_deployment(deployment, force_complete=False)` | `→ completed`; blocks on required interventions; cascades cleanup |

---

## Views and URLs (`/mobilization/`)

All views require `@login_required`. Mutation views require `@require_POST`.

| URL | View | Description |
|---|---|---|
| `/` | `overview` | Dashboard — responder counts, report pipeline, active events/deployments |
| `/responders/` | `responder_list` | Filterable list |
| `/responders/recruit/` | `responder_recruit` | Eligible persons not yet registered |
| `/responders/call-in/` | `responder_callin` | POST: creates Responder |
| `/responders/<pk>/` | `responder_detail` | Profile, skills, deployment history, badges |
| `/reports/` | `report_list` | Filterable by status and severity |
| `/reports/new/` | `report_create` | Create report |
| `/reports/<pk>/` | `report_detail` | Evidence list, enact/reject controls |
| `/reports/<pk>/submit/` | `report_submit` | POST: draft → submitted |
| `/reports/<pk>/review/` | `report_review` | POST: submitted → reviewed |
| `/reports/<pk>/enact/` | `report_enact` | POST: → enacted; optionally creates event |
| `/reports/<pk>/reject/` | `report_reject` | POST: → rejected |
| `/events/` | `event_list` | Filterable by status |
| `/events/<pk>/` | `event_detail` | Deployments, evac routes, escalation graph, mission timeline, emergency panel |
| `/events/<pk>/deployment/new/` | `deployment_create` | Create deployment; redirects to `response:deployment_detail` |
| `/events/<pk>/map-data/` | `event_map_data` | GeoJSON for overview map |
| `/events/<pk>/evac-routes/add/` | `evac_route_add` | POST: add evacuation route |
| `/events/<pk>/evac-routes/<pk>/status/` | `evac_route_status` | POST: update route status |
| `/events/<pk>/emergency/propose/` | `emergency_status_propose` | POST: create AssemblyProposal |
| `/events/<pk>/emergency/proposal/<pk>/activate/` | `emergency_proposal_activate` | POST: create EmergencyStatus from passed proposal |
| `/events/<pk>/emergency/<pk>/lift/` | `emergency_status_lift` | POST: lift emergency |
| `/events/<pk>/emergency/<pk>/equipment/` | `emergency_equipment_authorize` | POST: authorize hybrid equipment |

---

## Seeding

```bash
python manage.py ingress_mobilization
```

Seeds: `IncidentType` (8), `AchievementBadge` (8), `SkillBadge` group `mobilization` (7), up to 10 `Responder` records, then 5 full scenarios with reports, events, emergency statuses, and all deployment/response data (via `response` models). If no eligible persons exist, up to 6 are marked `is_federal_agent=True`.

## Dependencies

- `assembly` — EmergencyStatus.source_proposal — declaration requires a passed AssemblyProposal
- `assets` — Resource requests reference Asset
- `competence` — Responder skill requirements checked against SkillBadge
- `inventory` — Equipment requests linked to RealWorldObject
- `kanban` — Emergency tasks created as Kanban Mission/Task
- `locations` — Incident location is an Address FK
- `people` — EmergencyContact and responder Person FKs
- `response` — Deployment and DeploymentAssignment live in response app
- `socialhub` — EmergencyStatus is community-scoped
