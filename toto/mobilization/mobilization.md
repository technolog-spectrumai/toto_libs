# Mobilization App

Handles emergency response coordination: incident reports, field deployments, responder management, interventions, and emergency declarations.

---

## Core concepts

### Eligibility constraint
Responders **must** be either:
- `Person.is_federal_agent = True`, or
- A member of at least one `Community` with `is_federal_tribe = True`

This is enforced in `Responder.clean()` and in the call-in recruitment view. The ingress command bootstraps federal agents automatically if none exist.

### Emergency declaration constraint
Emergency status **cannot** be declared directly. It must go through an **assembly vote**:
1. Coordinator submits a proposal (`AssemblyProposal` type `emg_declare`) from the event detail page
2. Community members vote via the assembly system
3. Once the proposal reaches `passed` status, a coordinator clicks **Activate Emergency** — only then is `EmergencyStatus` created

This prevents unilateral emergency declarations and keeps a verifiable audit trail via `EmergencyStatus.source_proposal`.

---

## Data model

### Reference types (admin-only)

| Model | Purpose |
|---|---|
| `IncidentType` | Category of incident (flood, fire, earthquake…) — seeded by ingress |
| `InterventionType` | Category of intervention action (welfare-check, transport, sandbagging…) — seeded in migration 0006 |
| `AchievementBadge` | Badge definitions that can be awarded to persons for field service |

### Responder registry

```
Person ──OneToOne──► Responder ──M2M──► Community
                         │
                         ├──FK──► ResponderSkill ──FK──► competence.SkillBadge
                         └──(deployment_assignments, interventions)
```

- **`Responder`** — wrapper around `Person` representing a trained field agent. Tracks `current_status` (off_duty / available / standby / responding / unavailable), training flags, and community affiliations.
- **`ResponderSkill`** — links a responder to a `competence.SkillBadge` with a proficiency level (basic / trained / certified / professional). Optionally verified by a named person.
- **`PersonAchievement`** — awards an `AchievementBadge` to a person, optionally linked to a specific deployment and an awarding person.

### Incident pipeline

```
Detection ──evidence──► MobilizationReport ──enact──► MobilizationEvent
                                                             │
                                                     ┌───────┴────────┐
                                                  Deployment     EmergencyStatus
                                                     │                │
                                         ┌───────────┼──────────┐     └── EmergencyEquipmentAccess
                                  DeploymentAssignment  Intervention
                                  DeploymentRoute       DeploymentEquipment
                                  EvacuationRoute
```

#### MobilizationReport
Entry point for an incident. Lifecycle:

```
draft → submitted → reviewed → enacted
                 └──────────────────► rejected
```

- Only community heads or senior members can enact a report (`services.can_enact_report`).
- Severity is automatically recalculated from attached detection evidence (`services.update_report_severity_from_evidence`). Score = weighted average of detection severity + evidence role bump.
- Evidence weight `high` / `normal` / `low` maps to a multiplier (1.5 / 1.0 / 0.5). Primary evidence adds +1 to severity score; contradictory evidence subtracts 1.

#### MobilizationEvent
Created from an enacted report. Coordinates all deployments under a single incident.

- `is_hybrid` — indicates at least some deployments are partial-time.
- Links optionally to a `kanban.Campaign` (missions/tasks) and a `events.ScheduledEvent`.
- The `community` must match the source report's community (enforced in `clean()`).

#### EmergencyStatus
Declares a state of emergency for a community and/or zone under an event. Grants special access privileges:

| Flag | Effect |
|---|---|
| `allows_asset_requisition` | Community/zone assets may be requisitioned for deployment use |
| `allows_inventory_access` | Inventory items become available as hybrid equipment |
| `allows_route_commandeering` | Emergency vehicles may commandeer routes within the zone |

The `emergency_tax_rate` (e.g. `0.0250` = 2.5%) is the levy applied to transactions during the emergency. Tax revenue is collected into `emergency_tax_account` denominated in `emergency_tax_asset`.

`is_active` property returns `False` if status is not `active` or if `expires_at` has passed.

Lift via `es.lift(lifted_by=person)` — sets status to `lifted` and records `lifted_at`.

#### EmergencyEquipmentAccess
Tracks a specific inventory item (`inventory.RealWorldObject`) granted hybrid access under an active emergency. The item remains owned by the community/zone but can be allocated to a deployment. `is_hybrid = True` indicates the item is shared — ownership is not transferred.

### Deployment

```
MobilizationEvent ──FK──► Deployment ──FK──► DeploymentAssignment ──FK──► Responder
                               │
                               ├──FK──► DeploymentRoute ──FK──► locations.Route
                               ├──FK──► DeploymentEquipment ──FK──► inventory.RealWorldObject
                               └──FK──► Intervention ──FK──► InterventionType
                                                    └──FK──► detections.Detection
```

- `deployment_type`: evacuation / flood_response / fire_support / shelter_support / logistics / medical / welfare_check / reconnaissance / mixed / other
- `priority`: low / normal / high / urgent
- `status`: planned → active → paused → completed / cancelled
- `is_hybrid` + `hybrid_time_percent` (0–100) — marks partial-time deployments. The `is_partial` property aliases `is_hybrid`.
- `per_diem_amount` / `per_diem_asset` — allowance for assigned responders, denominated in an asset.
- `community` must match `event.community` (enforced in `clean()`).

Completion is blocked if any `is_required = True` interventions are not `done` or `cancelled`, unless `force_complete = True` is passed to `services.complete_deployment`.

#### DeploymentAssignment
Links a responder to a deployment with a role and status.

- Roles: lead / deputy / driver / medic / logistics / communicator / responder / volunteer
- Statuses: assigned → confirmed → active → released / completed / no_show
- Activating an assignment (`services.activate_deployment_assignment`) sets the responder's `current_status` to `responding`.
- Releasing (`services.release_responder_from_deployment`) resets responder to `available` if no other active assignment exists.

#### Intervention
A discrete task within a deployment. Lifecycle:

```
todo → assigned → in_progress → done
              └──► blocked
              └──► cancelled
```

- `intervention_type` — FK to `InterventionType` (admin-managed).
- `detection` — optional FK to a `detections.Detection` this intervention is mitigating.
- `is_required = True` blocks deployment completion.
- Review fields: `reviewer` (person), `reviewed_at`, `effect_description`.
- Cost fields: `estimated_cost`, `actual_cost`, `cost_asset`, `cost_center`.
- Reward fields: `reward_amount`, `reward_asset`, `reward_currency` — for compensating the assigned responder.

#### Routes
- **`EvacuationRoute`** — a `locations.Route` designated for a mobilization event. Types: evacuation / supply / medical / patrol / other. Statuses: planned / active / blocked / cleared.
- **`DeploymentRoute`** — a route assigned to a specific deployment. Types: primary / alternate / supply / retreat / other.

---

## Services (`services.py`)

| Function | Description |
|---|---|
| `can_enact_report(person, report)` | True if person is community head or senior member |
| `create_report_from_detection(community, detection, submitted_by)` | Creates report pre-linked to a detection as primary evidence |
| `add_detection_evidence(report, detection, ...)` | Adds evidence and recalculates report severity |
| `update_report_severity_from_evidence(report)` | Recalculates severity from all attached detections |
| `submit_report(report, submitted_by)` | draft → submitted |
| `review_report(report, reviewed_by)` | submitted → reviewed |
| `enact_report(report, enacted_by, create_event=False)` | submitted/reviewed → enacted; optionally creates event |
| `reject_report(report, rejected_by, notes)` | submitted/reviewed → rejected |
| `create_deployment(event, community, coordinator, **kwargs)` | Creates a deployment; validates community match |
| `assign_responder_to_deployment(deployment, responder, ...)` | Creates `DeploymentAssignment`; raises if already assigned |
| `activate_deployment_assignment(assignment)` | assignment → active; responder → responding |
| `release_responder_from_deployment(assignment)` | assignment → released; responder → available (if no other active) |
| `create_intervention(deployment, **kwargs)` | Creates an `Intervention` |
| `complete_intervention(intervention, outcome_notes)` | intervention → done |
| `complete_deployment(deployment, force_complete=False)` | deployment → completed; blocks on required interventions |

---

## Views and URLs

All views require `@login_required`. All mutation views require `@require_POST`.

### Responders
| URL | View | Description |
|---|---|---|
| `/responders/` | `responder_list` | Filterable list with status quick stats |
| `/responders/recruit/` | `responder_recruit` | Call-in roster: eligible persons not yet registered |
| `/responders/call-in/` | `responder_callin` | POST: creates a `Responder` after `full_clean()` |
| `/responders/<pk>/` | `responder_detail` | Profile, skills, deployment history, achievement badges |

### Reports
| URL | View | Description |
|---|---|---|
| `/reports/` | `report_list` | Filterable by status and severity |
| `/reports/new/` | `report_create` | Create a report with community, incident type, severity |
| `/reports/<pk>/` | `report_detail` | Evidence list, linked events, enact/reject controls |
| `/reports/<pk>/submit/` | `report_submit` | POST: draft → submitted |
| `/reports/<pk>/review/` | `report_review` | POST: submitted → reviewed |
| `/reports/<pk>/enact/` | `report_enact` | POST: → enacted; optionally creates event |
| `/reports/<pk>/reject/` | `report_reject` | POST: → rejected with notes |

### Events
| URL | View | Description |
|---|---|---|
| `/events/` | `event_list` | Filterable by status |
| `/events/<pk>/` | `event_detail` | Deployments, evac routes, escalation tree (Cytoscape.js), mission timeline (Chart.js), per-mission maps (Leaflet), emergency status panel |
| `/events/<pk>/deployment/new/` | `deployment_create` | Create a deployment under this event |
| `/events/<pk>/map-data/` | `event_map_data` | GeoJSON endpoint for the overview map |
| `/events/<pk>/evac-routes/add/` | `evac_route_add` | POST: add evacuation route |
| `/events/<pk>/evac-routes/<route_pk>/status/` | `evac_route_status` | POST: update route status |
| `/events/<pk>/emergency/propose/` | `emergency_status_propose` | POST: submit `AssemblyProposal` of type `emg_declare` |
| `/events/<pk>/emergency/proposal/<proposal_pk>/activate/` | `emergency_proposal_activate` | POST: create `EmergencyStatus` from a passed proposal |
| `/events/<pk>/emergency/<es_pk>/lift/` | `emergency_status_lift` | POST: lift an active emergency |
| `/events/<pk>/emergency/<es_pk>/equipment/` | `emergency_equipment_authorize` | POST: authorize an item as hybrid equipment |

### Deployments
| URL | View | Description |
|---|---|---|
| `/deployments/` | `deployment_list` | Filterable by status |
| `/deployments/<pk>/` | `deployment_detail` | Assignments, interventions with linked detections, routes, equipment, emergency equipment |
| `/deployments/<pk>/assign/` | `assignment_create` | POST: assign a responder |
| `/deployments/<pk>/activate/<assignment_pk>/` | `assignment_activate` | POST: assignment → active |
| `/deployments/<pk>/release/<assignment_pk>/` | `assignment_release` | POST: release responder |
| `/deployments/<pk>/complete/` | `deployment_complete` | POST: mark complete (blocks on required interventions) |
| `/deployments/<pk>/intervention/new/` | `intervention_create` | Create intervention; shows event-linked detections to mitigate |
| `/deployments/<pk>/routes/add/` | `deployment_route_add` | POST: add route to deployment |
| `/deployments/<pk>/equipment/add/` | `deployment_equipment_add` | POST: add inventory item to deployment |
| `/interventions/<pk>/done/` | `intervention_complete` | POST: intervention → done |
| `/interventions/<pk>/review/` | `intervention_review` | POST: set reviewer, effect description, reward |

---

## Responder status flow

```
off_duty ──► available ──► standby ──► responding
    ▲             ▲                        │
    │             └────────────────────────┘
    └─── unavailable (manual)
```

Status transitions happen automatically through service calls:
- `activate_deployment_assignment` → `responding`
- `release_responder_from_deployment` (no more active assignments) → `available`

---

## Emergency declaration flow

```
Event coordinator
    │
    ▼
[Propose Emergency] ──► AssemblyProposal (type=emg_declare, status=open)
                               │
                         Assembly vote
                               │
                    ┌──────────┴──────────┐
                 passed               rejected
                    │
                    ▼
          [Activate Emergency] ──► EmergencyStatus (status=active)
                                        │
                              [Authorize Equipment] ──► EmergencyEquipmentAccess
                                        │
                                   [Lift] ──► EmergencyStatus (status=lifted)
```

The `EmergencyStatus.source_proposal` FK links the activated status back to the assembly decision, forming an auditable chain.

---

## Intervention → detection link

When creating an intervention, the form shows detections attached to the event's source report as evidence. Selecting one sets `Intervention.detection` FK.

The deployment detail page shows a **Detections to Mitigate** panel listing all evidence detections with their severity and status. Each unlinked detection has an **Add Intervention** shortcut button that pre-fills the detection on the intervention form.

---

## Admin panel

All models are registered. Key restrictions:
- `InterventionType` — slug auto-populated from name. Modification here is the only way to add/edit types (no UI form).
- `AchievementBadge` — same pattern. Awards (`PersonAchievement`) are managed here or programmatically.
- `InterventionAdmin` uses `autocomplete_fields = ("intervention_type",)` — requires `search_fields` on `InterventionTypeAdmin`.
- `EmergencyStatus` has an inline for `EmergencyEquipmentAccess`.
- `Deployment` has inlines for `DeploymentAssignment` and `Intervention`.

---

## Seeding

```bash
python manage.py ingress_mobilization
```

Seeds in order:
1. `IncidentType` (8 records)
2. `InterventionType` (13 records — confirms migration-seeded count)
3. `AchievementBadge` (8 records across 5 categories)
4. `SkillBadge` group `mobilization` with 7 skill badges
5. `Responder` records (up to 10) from eligible persons; each gets 1–3 skills
6. 5 scenarios covering flood (active/critical), fire (active/high), storm (standby), chemical spill (resolved), mass casualty (report only)
7. Per scenario: `MobilizationReport`, `MobilizationReportEvidence` (if detections exist), `MobilizationEvent`, `EmergencyStatus`, `Deployment`, `DeploymentAssignment`, `DeploymentRoute`, `EvacuationRoute`, `DeploymentEquipment` (if inventory items exist), `Intervention`
8. `PersonAchievement` awards to responders who participated in deployments

If no eligible persons exist, the ingress marks up to 6 existing persons as `is_federal_agent = True` for demo purposes.

`DeploymentEquipment` and `EmergencyEquipmentAccess` seed only when `inventory.RealWorldObject` records exist (run inventory ingress first).

---

## Cross-app dependencies

| App | Usage |
|---|---|
| `people` | `Person` — responders, coordinators, reviewers |
| `socialhub` | `Community` — incident community, responder affiliation; `is_federal_tribe` eligibility gate |
| `locations` | `Route`, `Zone`, `Address` — evac routes, deployment routes, emergency zones |
| `inventory` | `RealWorldObject` — deployment equipment, hybrid emergency items |
| `assets` | `Asset`, `Currency`, `LedgerAccount` — per-diem, cost/reward denomination, emergency tax |
| `competence` | `SkillBadge`, `SkillGroup` — responder skills |
| `detections` | `Detection` — report evidence, intervention detection links |
| `kanban` | `Campaign`, `Mission`, `Task` — optional mission board overlay on events and deployments |
| `events` | `ScheduledEvent` — optional link from mobilization event to a calendar event |
| `assembly` | `AssemblyProposal` — emergency declaration approval gate |
