# Mobilization App

Handles emergency response coordination end-to-end: incident reporting, field deployments, responder management, task-level interventions, and formal emergency declarations with assembly-voted authorization.

---

## How it works

The app follows a strict pipeline from detection to resolution:

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
    └──► Deployment  (planned → active → paused → completed / cancelled)
              │
              ├──► DeploymentAssignment  ──► Responder
              ├──► Intervention  ──── mitigates ──► Detection
              ├──► DeploymentRoute  ──► locations.Route
              ├──► DeploymentEquipment  ──► inventory.RealWorldObject
              └──► EmergencyEquipmentAccess  (hybrid items under an active emergency)
```

---

## Internal model relationships

### Reference / lookup models

These are admin-managed and have no hard internal dependencies. They are FKed into operational models.

| Model | Used by |
|---|---|
| `IncidentType` | `MobilizationReport.incident_type`, `MobilizationEvent.incident_type` |
| `InterventionType` | `Intervention.intervention_type` |
| `AchievementBadge` | `PersonAchievement.badge` |

### Responder registry

```
people.Person ──OneToOne──► Responder ──M2M──► socialhub.Community
                                │
                                ├──FK (one-to-many)──► ResponderSkill ──FK──► competence.SkillBadge
                                ├──FK (one-to-many)──► DeploymentAssignment
                                └──FK (one-to-many)──► Intervention (assigned_to)
```

`Responder` wraps a `Person` and tracks operational state (`current_status`). Status transitions are managed exclusively through service calls — never set directly:

- `activate_deployment_assignment()` → `responding`
- `release_responder_from_deployment()` / `complete_deployment()` → `available` (if no other active assignment remains)

**Eligibility constraint** — `Responder.clean()` enforces that a person must be either `is_federal_agent=True` or a member of at least one `Community` with `is_federal_tribe=True`. This runs on every save.

`PersonAchievement` awards an `AchievementBadge` to a `Person`, optionally tied to the `Deployment` that earned it and the `Person` who awarded it. The `unique_together` on `(person, badge)` means each badge can only be awarded once per person.

### Report pipeline

```
MobilizationReport
    │
    ├──FK──► socialhub.Community (incident location)
    ├──FK──► IncidentType
    ├──FK──► people.Person  [submitted_by, reviewed_by, enacted_by]
    └──reverse FK──► MobilizationReportEvidence (evidence_links)
                          │
                          └──FK──► detections.Detection
```

Severity on `MobilizationReport` is **not manually set after creation** — it is recalculated automatically by `update_report_severity_from_evidence()` whenever evidence is added or updated. The algorithm:

1. For each linked detection: `score += (detection.severity_score + role_bump) × weight_factor`
   - `evidence_role` bumps: `primary +1`, `contradictory -1`, others `0`
   - `weight` factors: `high=1.5`, `normal=1.0`, `low=0.5`
2. `avg = total_score / evidence_count`
3. Thresholds: `≥2.5 → critical`, `≥1.8 → high`, `≥0.8 → medium`, else `low`

The `unique_together` on `(report, detection)` means each detection can only be linked once per report.

### Event and emergency

```
MobilizationEvent
    ├──FK──► socialhub.Community
    ├──FK──► MobilizationReport  (source_report — must share community)
    ├──FK──► events.ScheduledEvent  (optional calendar link)
    ├──FK──► kanban.Campaign  (optional mission board overlay)
    ├──FK──► people.Person  (coordinator)
    ├──reverse FK──► Deployment (deployments)
    ├──reverse FK──► EvacuationRoute (evac_routes)
    └──reverse FK──► EmergencyStatus (emergency_statuses)
```

`MobilizationEvent.clean()` validates that `community` matches `source_report.community` — this is the primary integrity constraint tying an event to its origin.

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

**Emergency declaration flow** — an emergency cannot be created directly. It requires an assembly vote:

1. Coordinator posts to `emergency_status_propose` → creates `AssemblyProposal(type="emg_declare")`
2. Community votes via the `assembly` app
3. Once `proposal.status == "passed"`, coordinator clicks **Activate Emergency** → `EmergencyStatus` is created with `source_proposal` set
4. `EmergencyStatus.source_proposal` forms an auditable chain back to the vote

`EmergencyEquipmentAccess` tracks an `inventory.RealWorldObject` granted hybrid access under an active emergency. The item stays owned by the community/zone; `is_hybrid=True` marks it as shared rather than transferred.

### Deployment subtree

```
Deployment
    ├──FK──► MobilizationEvent  (event — community must match)
    ├──FK──► socialhub.Community
    ├──FK──► kanban.Mission  (optional task board)
    ├──FK──► people.Person  (coordinator)
    │
    ├──reverse FK──► DeploymentAssignment
    │                    ├──FK──► Responder
    │                    └──FK──► people.Person  (assigned_by)
    │
    ├──reverse FK──► Intervention
    │                    ├──FK──► InterventionType
    │                    ├──FK──► detections.Detection  (detection being mitigated)
    │                    ├──FK──► Responder  (assigned_to)
    │                    ├──FK──► people.Person  (reported_by, reviewer)
    │                    ├──FK──► assets.Asset  (cost_asset, reward_asset)
    │                    ├──FK──► assets.Currency  (reward_currency)
    │                    └──FK──► kanban.Task  (optional task board link)
    │
    ├──reverse FK──► DeploymentRoute ──FK──► locations.Route
    ├──reverse FK──► DeploymentEquipment ──FK──► inventory.RealWorldObject
    └──reverse FK──► EmergencyEquipmentAccess (emergency_equipment)
```

`Deployment.clean()` validates that `community` matches `event.community`. Completing a deployment (`complete_deployment()`) is blocked unless all `is_required=True` interventions are `done` or `cancelled` — pass `force_complete=True` to override.

`DeploymentAssignment.unique_together = (deployment, responder)` — a responder can only be assigned once per deployment. Roles: lead / deputy / driver / medic / logistics / communicator / responder / volunteer.

---

## Cross-app dependencies

| App | Models used | Purpose |
|---|---|---|
| `people` | `Person` | Responders, coordinators, reviewers, enacted/submitted by |
| `socialhub` | `Community` | Incident community, responder affiliations, `is_federal_tribe` eligibility gate |
| `locations` | `Route`, `Zone` | Evacuation routes, deployment routes, emergency zone targeting |
| `inventory` | `RealWorldObject` | Deployment equipment, hybrid emergency items |
| `assets` | `Asset`, `Currency`, `LedgerAccount` | Intervention cost/reward denomination, emergency tax collection |
| `competence` | `SkillBadge` | Responder skill registry via `ResponderSkill` |
| `detections` | `Detection` | Report evidence (`MobilizationReportEvidence`), intervention mitigation target |
| `kanban` | `Campaign`, `Mission`, `Task` | Optional mission board overlay on events, deployments, interventions |
| `events` | `ScheduledEvent` | Optional calendar link from a mobilization event |
| `assembly` | `AssemblyProposal` | Emergency declaration authorization gate via `source_proposal` |

### Key coupling points

- **`detections` → `mobilization`**: The intervention creation form surfaces detections attached to the event's source report as candidates to mitigate. Setting `Intervention.detection` creates a traceable link from detected threat to field action.
- **`assembly` → `mobilization`**: `EmergencyStatus.source_proposal` is the only path to activating an emergency. The mobilization app reads `proposal.status` to gate the activate button — it never writes to `assembly`.
- **`socialhub.Community.is_federal_tribe`** and **`people.Person.is_federal_agent`**: These flags in external apps gate responder eligibility. Any change to those flags can silently break `Responder.clean()`.
- **`assets`** is used in two independent contexts: intervention cost/reward tracking (who bears the cost, what the responder earns) and emergency tax configuration (rate, denomination, destination account).

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
| `create_deployment(event, community, coordinator, ...)` | Creates `Deployment`; validates community match |
| `assign_responder_to_deployment(deployment, responder, ...)` | Creates `DeploymentAssignment`; raises if duplicate |
| `activate_deployment_assignment(assignment)` | `→ active`; responder `→ responding` |
| `release_responder_from_deployment(assignment)` | `→ released`; responder `→ available` if no other active assignment |
| `create_intervention(deployment, **kwargs)` | Creates `Intervention` |
| `complete_intervention(intervention, outcome_notes)` | `→ done` |
| `complete_deployment(deployment, force_complete=False)` | `→ completed`; blocks on required interventions; cascades assignment/responder cleanup |

---

## Seeding

```bash
python manage.py ingress_mobilization
```

Seeds in order: `IncidentType` (8), `InterventionType` (13), `AchievementBadge` (8), `SkillBadge` group `mobilization` (7), up to 10 `Responder` records from eligible persons, then 5 full scenarios (flood/fire/storm/chemical/mass-casualty) with reports, events, emergency statuses, deployments, assignments, routes, interventions, and achievement awards.

If no eligible persons exist, up to 6 existing persons are marked `is_federal_agent=True` for demo purposes. `DeploymentEquipment` and `EmergencyEquipmentAccess` only seed when `inventory.RealWorldObject` records exist — run inventory ingress first.
