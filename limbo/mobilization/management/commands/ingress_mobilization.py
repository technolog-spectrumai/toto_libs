import random
from django.utils import timezone
from django.db import models
from datetime import timedelta

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.mobilization.models import (
    AchievementBadge,
    PersonAchievement,
    IncidentType,
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationReportEvidence,
    MobilizationEvent,
    EmergencyStatus,
    EmergencyEquipmentAccess,
)
from toto.response.models import (
    InterventionType,
    Deployment,
    DeploymentAssignment,
    DeploymentEquipment,
    DeploymentRoute,
    EvacuationRoute,
    Intervention,
)


# ---------------------------------------------------------------------------
# Static seed data
# ---------------------------------------------------------------------------

INCIDENT_TYPES = [
    {"name": "Flood",                  "slug": "flood",                  "icon": "fa-solid fa-water",                    "order": 1},
    {"name": "Fire",                   "slug": "fire",                   "icon": "fa-solid fa-fire",                     "order": 2},
    {"name": "Earthquake",             "slug": "earthquake",             "icon": "fa-solid fa-house-crack",              "order": 3},
    {"name": "Storm",                  "slug": "storm",                  "icon": "fa-solid fa-cloud-bolt",               "order": 4},
    {"name": "Chemical Spill",         "slug": "chemical-spill",         "icon": "fa-solid fa-biohazard",                "order": 5},
    {"name": "Infrastructure Failure", "slug": "infrastructure-failure", "icon": "fa-solid fa-road-barrier",             "order": 6},
    {"name": "Mass Casualty",          "slug": "mass-casualty",          "icon": "fa-solid fa-hospital",                 "order": 7},
    {"name": "Evacuation",             "slug": "evacuation",             "icon": "fa-solid fa-person-walking-arrow-right","order": 8},
]

ACHIEVEMENT_BADGES = [
    {"name": "First Responder",       "slug": "first-responder",      "icon": "fa-solid fa-medal",               "category": "service",    "order": 1,
     "description": "Responded to a mobilization event within the first hour of activation."},
    {"name": "Life Saver",            "slug": "life-saver",           "icon": "fa-solid fa-heart-pulse",         "category": "rescue",     "order": 1,
     "description": "Directly involved in saving one or more lives during a deployment."},
    {"name": "Field Commander",       "slug": "field-commander",      "icon": "fa-solid fa-star",                "category": "leadership", "order": 1,
     "description": "Led a deployment team of 5+ responders through an active incident."},
    {"name": "Flood Specialist",      "slug": "flood-specialist",     "icon": "fa-solid fa-water",               "category": "training",   "order": 2,
     "description": "Completed certified flood response training and participated in a flood event."},
    {"name": "Search & Rescue",       "slug": "search-rescue",        "icon": "fa-solid fa-magnifying-glass-location", "category": "rescue", "order": 2,
     "description": "Successfully completed a search and rescue operation."},
    {"name": "Multi-Incident Veteran","slug": "multi-incident-veteran","icon": "fa-solid fa-shield-halved",      "category": "service",    "order": 3,
     "description": "Participated in 3 or more distinct mobilization events."},
    {"name": "Logistics Expert",      "slug": "logistics-expert",     "icon": "fa-solid fa-boxes-stacked",      "category": "training",   "order": 3,
     "description": "Managed supply chain and equipment allocation across a multi-deployment event."},
    {"name": "Night Ops",             "slug": "night-ops",            "icon": "fa-solid fa-moon",               "category": "special",    "order": 1,
     "description": "Completed a deployment that ran through overnight hours."},
]

RESPONDER_SKILL_SLUGS = [
    ("emergency-response",  "Emergency Response",  "First Response"),
    ("first-aid",           "First Aid & CPR",     "Medical"),
    ("navigation",          "Field Navigation",    "Field Ops"),
    ("comms",               "Radio Communications","Communications"),
    ("logistics-mob",       "Logistics & Supply",  "Logistics"),
    ("hazmat-basic",        "HazMat Basic",        "Safety"),
    ("water-rescue",        "Water Rescue",        "Rescue"),
]

SCENARIOS = [
    # ── Active critical flood ──────────────────────────────────────────────
    {
        "report_title": "Riverside District Flooding — Critical",
        "severity": "critical",
        "report_status": "enacted",
        "event_title": "Riverside Flood Response 2026",
        "event_status": "active",
        "incident": "flood",
        "hours_ago": 18,
        "emergency": {"level": "critical_emergency", "tax_rate": "0.0350", "zone_name": "Warsaw Old Town Zone"},
        "deployments": [
            {
                "title": "North Bank Evacuation Team",
                "deployment_type": "evacuation",
                "priority": "urgent",
                "status": "active",
                "objective": "Evacuate 200+ residents from low-lying zones along north bank",
                "is_hybrid": False,
                "interventions": [
                    {"title": "House-to-house checks — Block A",     "type": "welfare-check",  "priority": "urgent", "required": True,  "status": "done"},
                    {"title": "House-to-house checks — Block B",     "type": "welfare-check",  "priority": "urgent", "required": True,  "status": "in_progress"},
                    {"title": "Boat transport — elderly residents",  "type": "transport",      "priority": "urgent", "required": True,  "status": "in_progress"},
                    {"title": "Supply drop — temporary shelter",     "type": "supply-delivery","priority": "normal", "required": False, "status": "todo"},
                    {"title": "Evacuation pickup — Block C",         "type": "evacuation-pickup","priority":"high",  "required": True,  "status": "todo"},
                ],
            },
            {
                "title": "Medical Field Unit",
                "deployment_type": "medical",
                "priority": "high",
                "status": "active",
                "objective": "Provide immediate first aid and triage at assembly point",
                "is_hybrid": True,
                "hybrid_time_percent": 60,
                "interventions": [
                    {"title": "Triage — assembly point",                    "type": "first-aid",    "priority": "urgent", "required": True,  "status": "in_progress"},
                    {"title": "Damage report — flooded infrastructure",     "type": "damage-report","priority": "normal", "required": False, "status": "todo"},
                    {"title": "Decontamination station — evacuation hub",   "type": "decontamination","priority": "high", "required": True,  "status": "todo"},
                ],
            },
            {
                "title": "Sandbagging Crew — Levee Section 4",
                "deployment_type": "flood_response",
                "priority": "urgent",
                "status": "active",
                "objective": "Reinforce levee at section 4 before water reaches critical level",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Sandbagging — Levee Section 4A",  "type": "sandbagging",  "priority": "urgent", "required": True, "status": "in_progress"},
                    {"title": "Sandbagging — Levee Section 4B",  "type": "sandbagging",  "priority": "urgent", "required": True, "status": "todo"},
                    {"title": "Road closure — Riverside Drive",  "type": "road-closure", "priority": "urgent", "required": True, "status": "done"},
                ],
            },
        ],
    },

    # ── Active high fire ───────────────────────────────────────────────────
    {
        "report_title": "Industrial Fire — Sector 7 Warehouse District",
        "severity": "high",
        "report_status": "enacted",
        "event_title": "Warehouse Fire Containment — Sector 7",
        "event_status": "active",
        "incident": "fire",
        "hours_ago": 6,
        "emergency": {"level": "emergency", "tax_rate": "0.0200", "zone_name": "Warsaw Old Town Zone"},
        "deployments": [
            {
                "title": "Perimeter Control Team",
                "deployment_type": "reconnaissance",
                "priority": "high",
                "status": "active",
                "objective": "Establish and hold safe perimeter; monitor wind direction",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Road closure — N-access routes",          "type": "road-closure",  "priority": "urgent", "required": True,  "status": "done"},
                    {"title": "Road closure — S-access routes",          "type": "road-closure",  "priority": "urgent", "required": True,  "status": "done"},
                    {"title": "Welfare check — adjacent businesses",     "type": "welfare-check", "priority": "high",   "required": True,  "status": "in_progress"},
                    {"title": "Communications relay — sector command",   "type": "communications","priority": "high",   "required": True,  "status": "in_progress"},
                ],
            },
            {
                "title": "Logistics Coordination",
                "deployment_type": "logistics",
                "priority": "normal",
                "status": "active",
                "objective": "Coordinate water and foam supply to fire crews",
                "is_hybrid": True,
                "hybrid_time_percent": 50,
                "interventions": [
                    {"title": "Water truck routing",     "type": "transport",       "priority": "high",   "required": True,  "status": "in_progress"},
                    {"title": "Foam supply delivery",   "type": "supply-delivery", "priority": "high",   "required": True,  "status": "todo"},
                ],
            },
            {
                "title": "Shelter Setup — Community Hall",
                "deployment_type": "shelter_support",
                "priority": "normal",
                "status": "planned",
                "objective": "Open community hall as emergency shelter for displaced residents",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Shelter setup — main hall",   "type": "shelter-setup",  "priority": "normal", "required": True, "status": "todo"},
                    {"title": "Supply delivery — cots, food","type": "supply-delivery","priority": "normal", "required": True, "status": "todo"},
                ],
            },
        ],
    },

    # ── Standby storm ──────────────────────────────────────────────────────
    {
        "report_title": "Post-Storm Infrastructure Check — Northern Sector",
        "severity": "medium",
        "report_status": "enacted",
        "event_title": "Storm Aftermath — Northern Sector",
        "event_status": "standby",
        "incident": "storm",
        "hours_ago": 48,
        "emergency": None,
        "deployments": [
            {
                "title": "Damage Assessment Team",
                "deployment_type": "welfare_check",
                "priority": "normal",
                "status": "planned",
                "objective": "Survey storm damage across 15 km of northern roads",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Road condition survey — N17 corridor", "type": "damage-report", "priority": "normal", "required": True,  "status": "todo"},
                    {"title": "Sandbag deployment — flood risk zones", "type": "sandbagging",   "priority": "normal", "required": False, "status": "todo"},
                    {"title": "Search residential area — Block 9",    "type": "search-rescue", "priority": "normal", "required": False, "status": "todo"},
                ],
            },
        ],
    },

    # ── Resolved chemical spill (historical) ───────────────────────────────
    {
        "report_title": "Chemical Leak — Refinery Annex B",
        "severity": "high",
        "report_status": "closed",
        "event_title": "Refinery Chemical Spill Response",
        "event_status": "resolved",
        "incident": "chemical-spill",
        "hours_ago": 240,
        "emergency": {"level": "warning", "tax_rate": None, "status": "lifted"},
        "deployments": [
            {
                "title": "HazMat Containment Unit",
                "deployment_type": "mixed",
                "priority": "urgent",
                "status": "completed",
                "objective": "Contain chemical leak and decontaminate affected area",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Decontamination — Zone A",      "type": "decontamination", "priority": "urgent", "required": True, "status": "done"},
                    {"title": "Decontamination — Zone B",      "type": "decontamination", "priority": "urgent", "required": True, "status": "done"},
                    {"title": "Road closure — refinery access","type": "road-closure",     "priority": "urgent", "required": True, "status": "done"},
                    {"title": "Damage report — final",         "type": "damage-report",   "priority": "normal", "required": True, "status": "done"},
                ],
            },
            {
                "title": "Medical Response — Contamination Exposure",
                "deployment_type": "medical",
                "priority": "high",
                "status": "completed",
                "objective": "Treat workers and first responders exposed to chemical agent",
                "is_hybrid": False,
                "interventions": [
                    {"title": "First aid — 12 affected workers", "type": "first-aid", "priority": "urgent", "required": True, "status": "done"},
                    {"title": "Welfare check — refinery staff",  "type": "welfare-check","priority": "high",  "required": True, "status": "done"},
                ],
            },
        ],
    },

    # ── Submitted mass casualty (in review) ───────────────────────────────
    {
        "report_title": "Multi-Vehicle Collision — Highway 12 Junction",
        "severity": "critical",
        "report_status": "submitted",
        "event_title": None,
        "event_status": None,
        "incident": "mass-casualty",
        "hours_ago": 2,
        "emergency": None,
        "deployments": [],
    },
]

ROUTE_TYPE_EVAC_CHOICES = ["evacuation", "supply", "medical", "patrol"]
ROUTE_TYPE_DEP_CHOICES  = ["primary", "alternate", "supply", "retreat"]
RESPONDER_STATUSES = ["available", "standby", "available", "available", "responding", "off_duty"]


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

class Command(IngressCommand):
    help = "Seeds comprehensive mobilization data: every model type with realistic scenarios"

    def process(self):  # noqa: C901
        self._create_incident_types()
        self._create_intervention_types()
        achievement_cache = self._create_achievement_badges()

        communities = list(Community.objects.all())
        if not communities:
            print("⚠  No communities found — run ingress for socialhub first.")
            return

        from toto.people.civic import committed_citizen_filter
        from toto.socialhub.models import Constitution, ConstitutionSignature
        from django.utils import timezone

        federal_tribe_ids = list(
            Community.objects.filter(is_federal_tribe=True).values_list("id", flat=True)
        )
        eligible_persons = list(
            Person.objects.filter(
                committed_citizen_filter() | models.Q(communities__in=federal_tribe_ids)
            ).distinct().order_by("?")[:20]
        )

        if not eligible_persons:
            # Bootstrap: make candidates committed citizens by signing an active constitution
            candidates = list(Person.objects.order_by("?")[:6])
            if not candidates:
                print("⚠  No persons in database at all — run people/socialhub ingress first.")
                return
            constitution = Constitution.objects.filter(is_active=True).first()
            if constitution:
                now = timezone.now()
                for p in candidates:
                    ConstitutionSignature.objects.get_or_create(
                        constitution=constitution,
                        person=p,
                        defaults={"signed_at": now},
                    )
                eligible_persons = candidates
                print(f"  ℹ  No eligible persons — seeded {len(candidates)} as committed citizens via constitution signature.")
            else:
                print("⚠  No active constitution found — cannot seed committed citizens. Run socialhub ingress first.")
                return

        skill_cache = self._get_or_create_skill_badges()
        responders = self._create_responders(eligible_persons, communities, skill_cache)
        if not responders:
            print("⚠  Could not create responders.")
            return

        type_cache = {it.slug: it for it in InterventionType.objects.all()}

        # Grab optional linked models (may not exist yet)
        routes = self._get_routes()
        inventory_items = self._get_inventory_items()
        incidents = self._get_incidents()

        created_deployments = []
        for scenario in SCENARIOS:
            deps = self._create_scenario(scenario, responders, communities, type_cache, routes, inventory_items, incidents)
            created_deployments.extend(deps)

        self._award_achievements(responders, achievement_cache, created_deployments)
        self._enrich_with_locations()

        print(
            f"✔  Mobilization ingress complete: {len(SCENARIOS)} scenarios, "
            f"{len(responders)} responders, {len(created_deployments)} deployments."
        )

    # ── Reference data ───────────────────────────────────────────────────

    def _create_incident_types(self):
        for it in INCIDENT_TYPES:
            IncidentType.objects.get_or_create(
                slug=it["slug"],
                defaults={"name": it["name"], "icon": it["icon"], "order": it["order"]},
            )
        print(f"✔  Incident types: {IncidentType.objects.count()} total.")

    def _create_intervention_types(self):
        count = InterventionType.objects.count()
        print(f"✔  Intervention types: {count} total (seeded in migration).")

    def _create_achievement_badges(self):
        cache = {}
        for b in ACHIEVEMENT_BADGES:
            badge, _ = AchievementBadge.objects.get_or_create(
                slug=b["slug"],
                defaults={
                    "name": b["name"],
                    "icon": b["icon"],
                    "category": b["category"],
                    "order": b["order"],
                    "description": b["description"],
                },
            )
            cache[b["slug"]] = badge
        print(f"✔  Achievement badges: {AchievementBadge.objects.count()} total.")
        return cache

    def _get_or_create_skill_badges(self):
        from toto.competence.models import SkillBadge, SkillGroup
        cache = {}
        group, _ = SkillGroup.objects.get_or_create(
            slug="mobilization",
            defaults={"title": "Mobilization", "order": 50},
        )
        for slug, title, _ in RESPONDER_SKILL_SLUGS:
            badge, _ = SkillBadge.objects.get_or_create(
                group=group,
                slug=slug,
                defaults={"title": title, "icon": "fa-solid fa-shield-halved", "order": 0},
            )
            cache[slug] = badge
        print(f"✔  Responder skill badges: {len(cache)} total.")
        return cache

    # ── Responders ────────────────────────────────────────────────────────

    def _create_responders(self, persons, communities, skill_cache):
        responders = []
        skill_list = list(skill_cache.values())
        for person in persons[:10]:
            responder, _ = Responder.objects.get_or_create(
                person=person,
                defaults={
                    "is_active": True,
                    "is_trained": random.random() > 0.25,
                    "is_background_checked": random.random() > 0.35,
                    "current_status": random.choice(RESPONDER_STATUSES),
                    "notes": random.choice([
                        "Available for flood and evacuation operations.",
                        "Certified HazMat level 1.",
                        "Prefers field coordination over logistics.",
                        "Night-shift certified.",
                        "",
                    ]),
                },
            )
            if communities:
                responder.communities.add(random.choice(communities))
            for badge in random.sample(skill_list, min(random.randint(1, 3), len(skill_list))):
                ResponderSkill.objects.get_or_create(
                    responder=responder,
                    skill=badge,
                    defaults={"level": random.choice(["basic", "trained", "certified", "professional"])},
                )
            responders.append(responder)
        print(f"✔  Responders: {Responder.objects.count()} total.")
        return responders

    # ── Optional linked data helpers ──────────────────────────────────────

    def _get_routes(self):
        try:
            from toto.locations.models import Route
            routes = list(Route.objects.all())
            if routes:
                print(f"  ℹ  Found {len(routes)} routes for deployment/evac seeding.")
            return routes
        except Exception:
            return []

    def _get_inventory_items(self):
        try:
            from toto.inventory.models import RealWorldObject
            items = list(RealWorldObject.objects.select_related("location")[:20])
            if items:
                print(f"  ℹ  Found {len(items)} inventory items for equipment seeding.")
            return items
        except Exception:
            return []

    def _get_incidents(self):
        try:
            from toto.incidents.models import Incident
            incidents = list(Incident.objects.all()[:10])
            if incidents:
                print(f"  ℹ  Found {len(incidents)} incidents for evidence seeding.")
            return incidents
        except Exception:
            return []

    # ── Main scenario builder ─────────────────────────────────────────────

    def _create_scenario(self, scenario, responders, communities, type_cache, routes, inventory_items, incidents):  # noqa: C901
        community = random.choice(communities)
        coordinator = random.choice(responders).person if responders else None
        incident_type = IncidentType.objects.filter(slug=scenario["incident"]).first()
        now = timezone.now()
        hours_ago = scenario.get("hours_ago", 12)
        report_time = now - timedelta(hours=hours_ago)

        # ── Report ──
        report, _ = MobilizationReport.objects.get_or_create(
            title=scenario["report_title"],
            defaults={
                "community": community,
                "incident_type": incident_type,
                "severity": scenario["severity"],
                "status": scenario["report_status"],
                "summary": (
                    f"Emergency situation reported: {scenario['report_title']}. "
                    "Immediate response required. All available responders should be mobilized."
                ),
                "justification": (
                    "Situation confirmed by field agents. "
                    f"Incident type: {incident_type}. "
                    "Risk to life and property assessed as significant."
                ),
                "submitted_by": coordinator,
                "reviewed_by": coordinator if scenario["report_status"] in ("reviewed", "enacted", "closed") else None,
                "enacted_by": coordinator if scenario["report_status"] in ("enacted", "closed") else None,
                "enacted_at": report_time + timedelta(hours=1) if scenario["report_status"] in ("enacted", "closed") else None,
            },
        )

        # Seed report evidence from available incidents
        if incidents and scenario["report_status"] in ("enacted", "closed", "reviewed"):
            for inc in random.sample(incidents, min(2, len(incidents))):
                MobilizationReportEvidence.objects.get_or_create(
                    report=report,
                    incident=inc,
                    defaults={
                        "evidence_role": random.choice(["primary", "supporting", "context"]),
                        "weight": random.choice(["normal", "high"]),
                        "note": "Auto-linked by ingress.",
                        "added_by": coordinator,
                    },
                )

        # ── Event (skip if no event for this scenario) ──
        if not scenario.get("event_title"):
            print(f"  ✔ Report only: {scenario['report_title']}")
            return []

        event, _ = MobilizationEvent.objects.get_or_create(
            title=scenario["event_title"],
            defaults={
                "community": community,
                "source_report": report,
                "incident_type": incident_type,
                "status": scenario["event_status"],
                "coordinator": coordinator,
                "is_hybrid": any(d.get("is_hybrid") for d in scenario["deployments"]),
                "description": (
                    f"Response operation for: {report.title}. "
                    "Coordinate all deployments through this event."
                ),
                "started_at": report_time + timedelta(hours=2) if scenario["event_status"] in ("active", "resolved") else None,
                "ended_at": report_time + timedelta(hours=hours_ago - 1) if scenario["event_status"] == "resolved" else None,
            },
        )

        # ── Emergency status ──
        emrg_data = scenario.get("emergency")
        if emrg_data:
            emrg_status = emrg_data.get("status", "active")
            # Resolve optional zone by name
            emrg_zone = None
            if emrg_data.get("zone_name"):
                try:
                    from toto.locations.models import Zone as _Zone
                    emrg_zone = _Zone.objects.filter(name=emrg_data["zone_name"]).first()
                except Exception:
                    pass
            es, es_created = EmergencyStatus.objects.get_or_create(
                event=event,
                community=community,
                defaults={
                    "zone": emrg_zone,
                    "level": emrg_data["level"],
                    "status": emrg_status,
                    "declared_by": coordinator,
                    "declared_at": report_time + timedelta(hours=3),
                    "lifted_at": report_time + timedelta(hours=hours_ago - 2) if emrg_status == "lifted" else None,
                    "allows_asset_requisition": True,
                    "allows_inventory_access": True,
                    "allows_route_commandeering": emrg_data["level"] in ("emergency", "critical_emergency"),
                    "emergency_tax_rate": emrg_data.get("tax_rate"),
                    "notes": f"Emergency declared in response to: {event.title}",
                },
            )

            # Seed hybrid equipment access if we have inventory items
            if es_created and inventory_items and emrg_data.get("tax_rate"):
                for item in random.sample(inventory_items, min(2, len(inventory_items))):
                    EmergencyEquipmentAccess.objects.get_or_create(
                        emergency=es,
                        item=item,
                        defaults={
                            "is_hybrid": True,
                            "quantity": random.choice([1, 2, 5, 10]),
                            "authorized_by": coordinator,
                        },
                    )

        # ── Deployments ──
        created_deployments = []
        available_responders = [r for r in responders if r.current_status in ("available", "standby", "responding")]

        for dep_data in scenario["deployments"]:
            dep, dep_created = Deployment.objects.get_or_create(
                event=event,
                title=dep_data["title"],
                defaults={
                    "community": community,
                    "deployment_type": dep_data["deployment_type"],
                    "priority": dep_data["priority"],
                    "status": dep_data["status"],
                    "coordinator": coordinator,
                    "objective": dep_data.get("objective", ""),
                    "is_hybrid": dep_data.get("is_hybrid", False),
                    "hybrid_time_percent": dep_data.get("hybrid_time_percent"),
                },
            )

            if dep_created:
                # Assignments
                assigned = random.sample(available_responders, min(random.randint(1, 3), len(available_responders)))
                lead = assigned[0] if assigned else None
                for i, responder in enumerate(assigned):
                    role = "lead" if i == 0 else random.choice(["responder", "medic", "driver", "communicator"])
                    a_status = "active" if dep_data["status"] == "active" else (
                        "completed" if dep_data["status"] == "completed" else "assigned"
                    )
                    DeploymentAssignment.objects.get_or_create(
                        deployment=dep,
                        responder=responder,
                        defaults={"role": role, "status": a_status, "assigned_by": coordinator},
                    )

                # Evacuation routes (for evacuation-type deployments)
                if routes and dep_data["deployment_type"] in ("evacuation", "flood_response", "medical"):
                    for route in random.sample(routes, min(2, len(routes))):
                        evac_status = "cleared" if dep_data["status"] == "completed" else random.choice(["active", "planned"])
                        EvacuationRoute.objects.get_or_create(
                            event=event,
                            route=route,
                            defaults={
                                "name": f"{route.name} — {dep_data['deployment_type'].replace('_', ' ').title()}",
                                "route_type": random.choice(ROUTE_TYPE_EVAC_CHOICES),
                                "status": evac_status,
                                "notes": f"Assigned for {dep.title}.",
                            },
                        )

                # Deployment routes
                if routes:
                    for route in random.sample(routes, min(2, len(routes))):
                        DeploymentRoute.objects.get_or_create(
                            deployment=dep,
                            route=route,
                            defaults={
                                "route_type": random.choice(ROUTE_TYPE_DEP_CHOICES),
                                "notes": f"Route designated for {dep.title}.",
                            },
                        )

                # Equipment
                if inventory_items:
                    for item in random.sample(inventory_items, min(random.randint(1, 3), len(inventory_items))):
                        DeploymentEquipment.objects.get_or_create(
                            deployment=dep,
                            item=item,
                            defaults={
                                "quantity": random.choice([1, 2, 4, 10, 20]),
                                "notes": f"Allocated to {dep.title}.",
                            },
                        )

            # Interventions
            for iv_data in dep_data.get("interventions", []):
                iv_type = type_cache.get(iv_data["type"])
                iv_status = iv_data.get("status", "todo")
                iv, _ = Intervention.objects.get_or_create(
                    deployment=dep,
                    title=iv_data["title"],
                    defaults={
                        "intervention_type": iv_type,
                        "priority": iv_data["priority"],
                        "is_required": iv_data.get("required", True),
                        "status": iv_status,
                        "reported_by": coordinator,
                        "started_at": report_time + timedelta(hours=4) if iv_status in ("in_progress", "done") else None,
                        "completed_at": report_time + timedelta(hours=6) if iv_status == "done" else None,
                        "outcome_notes": "Completed successfully." if iv_status == "done" else "",
                        "effect_description": (
                            "Situation stabilised after intervention." if iv_status == "done" else ""
                        ),
                        "estimated_cost": random.choice([None, "500.00", "1200.00", "300.00"]),
                    },
                )

            created_deployments.append(dep)

        print(f"  ✔ Scenario: {scenario['event_title']} ({len(created_deployments)} deployments)")
        return created_deployments

    # ── Location + equipment enrichment ──────────────────────────────────

    def _enrich_with_locations(self):  # noqa: C901
        """
        Post-scenario hook: wires up Warsaw-area map data so every page has
        visible markers — kanban mission address, campaign zone, defibrillator
        equipment, evac routes, and deployment→mission links.
        """
        # ── Imports ──
        try:
            from toto.kanban.models import Mission, Campaign
            from toto.locations.models import Address, Zone, Route
            from toto.inventory.models import ObjectType, RealWorldObject
        except Exception as e:
            print(f"  ⚠  Location enrichment skipped (import error): {e}")
            return

        addr_royal_castle = Address.objects.filter(street="Royal Castle", locality_name="Warsaw").first()
        addr_palace_culture = Address.objects.filter(street="Palace of Culture and Science", locality_name="Warsaw").first()
        warsaw_zone = Zone.objects.filter(name="Warsaw Old Town Zone").first()
        polish_trail = Route.objects.filter(name="Polish Royal Trail").first()

        # ── 1. "Mitigate Active Detections" mission → Warsaw Royal Castle address ──
        mission = Mission.objects.filter(title="Mitigate Active Detections").first()
        if mission:
            if addr_royal_castle and not mission.location_id:
                mission.location = addr_royal_castle
                mission.save(update_fields=["location"])
                print("  ✔ Added Warsaw Royal Castle address to 'Mitigate Active Detections' mission")

            # ── 2. "Field Response" campaign → Warsaw Old Town Zone ──
            if warsaw_zone and mission.campaign_id:
                campaign = mission.campaign
                if not campaign.zone_id:
                    campaign.zone = warsaw_zone
                    campaign.save(update_fields=["zone"])
                    print("  ✔ Added Warsaw Old Town Zone to 'Field Response' campaign")

            # ── 3. Link flood event → campaign, flood deployment → mission ──
            flood_event = MobilizationEvent.objects.filter(title="Riverside Flood Response 2026").first()
            if flood_event:
                if mission.campaign_id and not flood_event.kanban_campaign_id:
                    flood_event.kanban_campaign = mission.campaign
                    flood_event.save(update_fields=["kanban_campaign"])
                    print("  ✔ Linked 'Riverside Flood Response 2026' → 'Field Response' campaign")
                evac_dep = flood_event.deployments.filter(title="North Bank Evacuation Team").first()
                if evac_dep and not evac_dep.kanban_mission_id:
                    evac_dep.kanban_mission = mission
                    evac_dep.save(update_fields=["kanban_mission"])
                    print("  ✔ Linked 'North Bank Evacuation Team' deployment → 'Mitigate Active Detections' mission")

                # ── 4. Ensure flood event has Polish Royal Trail as evac route ──
                if polish_trail:
                    EvacuationRoute.objects.get_or_create(
                        event=flood_event,
                        route=polish_trail,
                        defaults={
                            "name": "Polish Royal Trail — Emergency Evacuation Corridor",
                            "route_type": "evacuation",
                            "status": "active",
                            "notes": "Primary evacuation corridor through Warsaw Old Town towards Krakow.",
                        },
                    )
                    if evac_dep:
                        DeploymentRoute.objects.get_or_create(
                            deployment=evac_dep,
                            route=polish_trail,
                            defaults={
                                "route_type": "primary",
                                "notes": "Main route for North Bank evacuation buses.",
                            },
                        )
                    print(f"  ✔ Added 'Polish Royal Trail' as evac/deployment route for flood event")
        else:
            print("  ℹ  'Mitigate Active Detections' mission not found — run ingress_detections first for full kanban links")

        # ── 5. Defibrillator at Warsaw Palace of Culture ──
        if not addr_palace_culture:
            print("  ⚠  Warsaw Palace of Culture address not found — run ingress_locations first")
        else:
            med_type, _ = ObjectType.objects.get_or_create(
                slug="medical-equipment",
                defaults={
                    "name": "Medical Equipment",
                    "description": "Medical devices and emergency health equipment.",
                },
            )
            aed, aed_created = RealWorldObject.objects.get_or_create(
                slug="aed-warsaw-palace",
                defaults={
                    "name": "Automated External Defibrillator",
                    "object_type": med_type,
                    "description": (
                        "AED unit stationed at Warsaw Palace of Culture and Science. "
                        "Certified for public emergency use. Available for medical deployments."
                    ),
                    "location": addr_palace_culture,
                },
            )
            if aed_created:
                print("  ✔ Created Automated External Defibrillator at Warsaw Palace of Culture")
            # Update location if it was created without one
            elif not aed.location_id and addr_palace_culture:
                aed.location = addr_palace_culture
                aed.save(update_fields=["location"])

            med_dep = Deployment.objects.filter(title="Medical Response — Contamination Exposure").first()
            if med_dep:
                eq, eq_created = DeploymentEquipment.objects.get_or_create(
                    deployment=med_dep,
                    item=aed,
                    defaults={
                        "quantity": 2,
                        "notes": "AED units — stored at Warsaw Palace of Culture. Deploy to treatment area on arrival.",
                    },
                )
                if eq_created:
                    print("  ✔ Assigned AED to 'Medical Response — Contamination Exposure'")
            else:
                print("  ⚠  'Medical Response — Contamination Exposure' deployment not found")

    # ── Achievement awards ────────────────────────────────────────────────

    def _award_achievements(self, responders, achievement_cache, deployments):
        if not achievement_cache or not responders:
            return

        badge_list = list(achievement_cache.values())
        persons_with_deployments = set()
        for dep in deployments:
            for a in dep.assignments.select_related("responder__person"):
                persons_with_deployments.add(a.responder.person_id)

        awarded = 0
        for responder in responders:
            person = responder.person
            # Award 1-3 badges to each active responder who participated in a deployment
            if person.pk not in persons_with_deployments:
                continue
            n = random.randint(1, min(3, len(badge_list)))
            for badge in random.sample(badge_list, n):
                linked_dep = random.choice(deployments) if deployments else None
                _, created = PersonAchievement.objects.get_or_create(
                    person=person,
                    badge=badge,
                    defaults={
                        "deployment": linked_dep,
                        "awarded_by": random.choice(responders).person,
                        "awarded_at": timezone.now() - timedelta(days=random.randint(0, 30)),
                        "note": f"Awarded for outstanding performance during: {linked_dep.title if linked_dep else 'field operations'}.",
                    },
                )
                if created:
                    awarded += 1

        print(f"✔  Achievement awards: {awarded} new awards given.")
