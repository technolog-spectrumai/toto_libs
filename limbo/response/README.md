# Response App

Field operations layer for emergency response. Handles everything that happens after a `mobilization.MobilizationEvent` is created: deployments, responder assignments, interventions, routes, and equipment. The upstream command pipeline lives in the `mobilization` app.

---

## How it works

```
mobilization.MobilizationEvent  ◄── FK anchor (owned by mobilization)
    │
    └──► Deployment  (planned → active → paused → completed / cancelled)
              │
              ├──► DeploymentAssignment  ──► mobilization.Responder
              │         (assigned → confirmed → active → released / completed / no_show)
              │
              ├──► Intervention  ──── mitigates ──► detections.Detection
              │         (todo → assigned → in_progress → done / cancelled)
              │
              ├──► DeploymentRoute  ──► locations.Route
              ├──► DeploymentEquipment  ──► inventory.RealWorldObject
              └──► EmergencyEquipmentAccess  (from mobilization.EmergencyStatus)

mobilization.MobilizationEvent
    └──► EvacuationRoute  ──► locations.Route
              (planned / active / blocked / cleared)
```

---

## Internal model relationships

### Reference model

`InterventionType` — admin-managed lookup used by `Intervention.intervention_type`. Types are seeded by `ingress_mobilization` (13 records). The only way to add/edit types is the Django admin (no UI form).

### Deployment subtree

```
Deployment
    ├──FK──► mobilization.MobilizationEvent  (event)
    ├──FK──► socialhub.Community  (must match event.community)
    ├──FK──► kanban.Mission  (optional mission board link)
    ├──FK──► people.Person  (coordinator)
    │
    ├──reverse FK──► DeploymentAssignment
    │                    ├──FK──► mobilization.Responder
    │                    └──FK──► people.Person  (assigned_by)
    │
    ├──reverse FK──► Intervention
    │                    ├──FK──► InterventionType
    │                    ├──FK──► detections.Detection  (threat being mitigated)
    │                    ├──FK──► mobilization.Responder  (assigned_to)
    │                    ├──FK──► people.Person  (reported_by, reviewer)
    │                    ├──FK──► assets.Asset  (cost_asset, reward_asset)
    │                    ├──FK──► assets.Currency  (reward_currency)
    │                    └──FK──► kanban.Task  (optional task board link)
    │
    ├──reverse FK──► DeploymentRoute ──FK──► locations.Route
    └──reverse FK──► DeploymentEquipment ──FK──► inventory.RealWorldObject
```

**Integrity constraint** — `Deployment.clean()` validates `community == event.community`. This is also enforced by `mobilization.services.create_deployment()`.

**Completion guard** — `complete_deployment()` raises `ValidationError` if any `Intervention` with `is_required=True` is not in `done` or `cancelled` state. Pass `force_complete=True` to bypass.

### DeploymentAssignment

Links a `mobilization.Responder` to a `Deployment` with a role and lifecycle status.

- Roles: lead / deputy / driver / medic / logistics / communicator / responder / volunteer
- `unique_together = (deployment, responder)` — one assignment per responder per deployment
- `activate_deployment_assignment()` sets `responder.current_status = "responding"`
- `release_responder_from_deployment()` sets `responder.current_status = "available"` if no other active assignment remains

### Intervention

A discrete task within a deployment. Can be linked to a `detections.Detection` it is intended to mitigate, creating a traceable chain from detected threat → field action.

- `is_required = True` blocks `complete_deployment()` until resolved
- Cost fields: `estimated_cost`, `actual_cost`, `cost_asset`, `cost_center` — who bears the cost
- Reward fields: `reward_amount`, `reward_asset`, `reward_currency` — compensation for the assigned responder
- Review fields: `reviewer` (person), `reviewed_at`, `effect_description` — post-action assessment

### Routes

- **`EvacuationRoute`** — a `locations.Route` designated at the event level. Managed from the mobilization `event_detail` page (evac route views stay in `mobilization`). Statuses: planned / active / blocked / cleared.
- **`DeploymentRoute`** — a route assigned to a specific deployment. Types: primary / alternate / supply / retreat / other.

---

## Cross-app dependencies

| App | Models used | Purpose |
|---|---|---|
| **`mobilization`** | `MobilizationEvent`, `Responder` | FK anchor for deployments/evac routes; responder registry |
| `people` | `Person` | Coordinators, assignees, reviewers |
| `socialhub` | `Community` | Deployment community (must match event community) |
| `locations` | `Route` | Evacuation and deployment routes |
| `inventory` | `RealWorldObject` | Deployment equipment |
| `assets` | `Asset`, `Currency` | Intervention cost/reward denomination |
| `detections` | `Detection` | Intervention mitigation target; report evidence (read-only) |
| `kanban` | `Mission`, `Task` | Optional mission board overlay on deployments/interventions |

### Key coupling points

- **`mobilization.Responder` status** is mutated by response-side service calls. `response` owns the transitions (`responding` / `available`) but the `Responder` model lives in `mobilization`.
- **`detections.Detection`** is linked to an `Intervention` via `Intervention.detection`. The deployment detail page surfaces evidence detections from the event's source report to suggest which detections need a mitigation intervention.
- **`mobilization.EmergencyEquipmentAccess.deployment`** FKs into `response.Deployment` — when an emergency grants hybrid equipment access, it is tied to a specific deployment from this app.

---

## Services (in `mobilization/services.py`)

Response operations are intentionally orchestrated from `mobilization.services` to keep business logic centralized. The service functions import from `response.models` as needed.

| Function | What it does |
|---|---|
| `create_deployment(event, community, coordinator, ...)` | Creates `Deployment`; validates community match |
| `assign_responder_to_deployment(deployment, responder, ...)` | Creates `DeploymentAssignment`; raises on duplicate |
| `activate_deployment_assignment(assignment)` | `→ active`; responder `→ responding` |
| `release_responder_from_deployment(assignment)` | `→ released`; responder `→ available` if no other active |
| `create_intervention(deployment, **kwargs)` | Creates `Intervention` |
| `complete_intervention(intervention, outcome_notes)` | `→ done` |
| `complete_deployment(deployment, force_complete=False)` | `→ completed`; blocks on required interventions; cascades assignment/responder cleanup |

---

## Views and URLs (`/response/`)

All views require `@login_required`. Mutation views require `@require_POST`.

| URL | View | Description |
|---|---|---|
| `/deployments/` | `deployment_list` | Filterable by status |
| `/deployments/<pk>/` | `deployment_detail` | Assignments, interventions with linked detections, routes, equipment |
| `/deployments/<pk>/map-data/` | `deployment_map_data` | GeoJSON for deployment map |
| `/deployments/<pk>/assign/` | `assignment_create` | POST: assign a responder |
| `/deployments/<pk>/activate/<assignment_pk>/` | `assignment_activate` | POST: assignment → active |
| `/deployments/<pk>/release/<assignment_pk>/` | `assignment_release` | POST: release responder |
| `/deployments/<pk>/complete/` | `deployment_complete` | POST: mark complete |
| `/deployments/<pk>/intervention/new/` | `intervention_create` | Create intervention; shows event evidence detections |
| `/deployments/<pk>/routes/add/` | `deployment_route_add` | POST: add route |
| `/deployments/<pk>/equipment/add/` | `deployment_equipment_add` | POST: add inventory item |
| `/interventions/<pk>/done/` | `intervention_complete` | POST: → done |
| `/interventions/<pk>/review/` | `intervention_review` | POST: set reviewer, effect description, reward |

> **Note:** Deployment creation (`/mobilization/events/<pk>/deployment/new/`) and evacuation route management stay in the `mobilization` app since they are event-level actions. After creation, navigation redirects to `response:deployment_detail`.

---

## Responder status flow

```
off_duty ──► available ──► standby ──► responding
    ▲             ▲                        │
    │             └────────────────────────┘
    └─── unavailable (manual)
```

Transitions happen automatically through service calls:
- `activate_deployment_assignment()` → `responding`
- `release_responder_from_deployment()` (no more active assignments) → `available`
- `complete_deployment()` — cascades release for all active assignments

---

## Seeding

Seeded by `python manage.py ingress_mobilization` alongside mobilization data. Requires inventory ingress to have run first for `DeploymentEquipment` and `EmergencyEquipmentAccess` records to be created.

## Dependencies

- `assets` — Intervention cost/reward denominated in Asset
- `inventory` — DeploymentEquipment links to RealWorldObject
- `kanban` — Deployment may link to Kanban Mission
- `locations` — EvacuationRoute and DeploymentRoute reference Address
- `mobilization` — Deployment FK to mobilization.EmergencyEvent
- `people` — DeploymentAssignment assignee is a Person
- `socialhub` — Deployment.community scoping
