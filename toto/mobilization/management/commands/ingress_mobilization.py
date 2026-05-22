import random
from django.utils import timezone
from datetime import timedelta

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community
from toto.mobilization.models import (
    IncidentType,
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationEvent,
    Deployment,
    DeploymentAssignment,
    Intervention,
    EvacuationRoute,
    DeploymentRoute,
    DeploymentEquipment,
)


INCIDENT_TYPES = [
    {"name": "Flood", "slug": "flood", "icon": "fa-solid fa-water", "order": 1},
    {"name": "Fire", "slug": "fire", "icon": "fa-solid fa-fire", "order": 2},
    {"name": "Earthquake", "slug": "earthquake", "icon": "fa-solid fa-house-crack", "order": 3},
    {"name": "Storm", "slug": "storm", "icon": "fa-solid fa-cloud-bolt", "order": 4},
    {"name": "Chemical Spill", "slug": "chemical-spill", "icon": "fa-solid fa-biohazard", "order": 5},
    {"name": "Infrastructure Failure", "slug": "infrastructure-failure", "icon": "fa-solid fa-road-barrier", "order": 6},
    {"name": "Mass Casualty", "slug": "mass-casualty", "icon": "fa-solid fa-hospital", "order": 7},
    {"name": "Evacuation", "slug": "evacuation", "icon": "fa-solid fa-person-walking-arrow-right", "order": 8},
]

SCENARIOS = [
    {
        "report_title": "Riverside District Flooding — Critical",
        "severity": "critical",
        "event_title": "Riverside Flood Response 2026",
        "event_status": "active",
        "incident": "flood",
        "deployments": [
            {
                "title": "North Bank Evacuation Team",
                "deployment_type": "evacuation",
                "priority": "urgent",
                "status": "active",
                "objective": "Evacuate 200+ residents from low-lying zones along north bank",
                "is_hybrid": False,
                "interventions": [
                    {"title": "House-to-house checks — Block A", "intervention_type": "welfare_check", "priority": "urgent", "is_required": True},
                    {"title": "Boat transport — elderly residents", "intervention_type": "transport", "priority": "urgent", "is_required": True},
                    {"title": "Supply drop — temporary shelter", "intervention_type": "supply_delivery", "priority": "normal", "is_required": False},
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
                    {"title": "Triage — assembly point", "intervention_type": "first_aid", "priority": "urgent", "is_required": True},
                    {"title": "Damage report — flooded infrastructure", "intervention_type": "damage_report", "priority": "normal", "is_required": False},
                ],
            },
        ],
    },
    {
        "report_title": "Industrial Fire — Sector 7 Warehouse District",
        "severity": "high",
        "event_title": "Warehouse Fire Containment — Sector 7",
        "event_status": "active",
        "incident": "fire",
        "deployments": [
            {
                "title": "Perimeter Control Team",
                "deployment_type": "reconnaissance",
                "priority": "high",
                "status": "active",
                "objective": "Establish and hold safe perimeter; monitor wind direction",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Road closure — access routes", "intervention_type": "road_closure", "priority": "urgent", "is_required": True},
                    {"title": "Welfare check — adjacent businesses", "intervention_type": "welfare_check", "priority": "high", "is_required": True},
                ],
            },
            {
                "title": "Logistics Coordination",
                "deployment_type": "logistics",
                "priority": "normal",
                "status": "planned",
                "objective": "Coordinate water and foam supply to fire crews",
                "is_hybrid": True,
                "hybrid_time_percent": 50,
                "interventions": [
                    {"title": "Water truck routing", "intervention_type": "transport", "priority": "high", "is_required": True},
                ],
            },
        ],
    },
    {
        "report_title": "Post-Storm Infrastructure Check — Northern Sector",
        "severity": "medium",
        "event_title": "Storm Aftermath — Northern Sector",
        "event_status": "standby",
        "incident": "storm",
        "deployments": [
            {
                "title": "Damage Assessment Team",
                "deployment_type": "welfare_check",
                "priority": "normal",
                "status": "planned",
                "objective": "Survey storm damage across 15 km of northern roads",
                "is_hybrid": False,
                "interventions": [
                    {"title": "Road condition survey — N17 corridor", "intervention_type": "damage_report", "priority": "normal", "is_required": True},
                    {"title": "Sandbag deployment — flood risk zones", "intervention_type": "sandbagging", "priority": "normal", "is_required": False},
                ],
            },
        ],
    },
]

RESPONDER_STATUSES = ["available", "standby", "available", "available", "responding", "off_duty"]


class Command(IngressCommand):
    help = "Seeds realistic mobilization scenarios: incident types, responders, reports, events, deployments, and interventions"

    def process(self):
        self._create_incident_types()

        persons = list(Person.objects.order_by("?")[:20])
        communities = list(Community.objects.all())

        if not persons:
            print("⚠  No persons found — run ingress for people/socialhub first.")
            return
        if not communities:
            print("⚠  No communities found — run ingress for socialhub first.")
            return

        responders = self._create_responders(persons, communities)
        if not responders:
            print("⚠  Could not create responders.")
            return

        for scenario in SCENARIOS:
            self._create_scenario(scenario, responders, communities)

        print(f"✔  Mobilization ingress complete: {len(SCENARIOS)} scenarios, {len(responders)} responders.")

    def _create_incident_types(self):
        for it in INCIDENT_TYPES:
            IncidentType.objects.get_or_create(
                slug=it["slug"],
                defaults={"name": it["name"], "icon": it["icon"], "order": it["order"]},
            )
        print(f"✔  Incident types: {IncidentType.objects.count()} total.")

    def _create_responders(self, persons, communities):
        responders = []
        for i, person in enumerate(persons[:8]):
            responder, created = Responder.objects.get_or_create(
                person=person,
                defaults={
                    "is_active": True,
                    "is_trained": random.random() > 0.3,
                    "is_background_checked": random.random() > 0.4,
                    "current_status": random.choice(RESPONDER_STATUSES),
                },
            )
            # Assign to communities
            if communities:
                responder.communities.add(random.choice(communities))
            responders.append(responder)
        if created or True:
            print(f"✔  Responders: {Responder.objects.count()} total.")
        return responders

    def _create_scenario(self, scenario, responders, communities):
        community = random.choice(communities)
        coordinator = random.choice(responders).person if responders else None
        incident_type = IncidentType.objects.filter(slug=scenario["incident"]).first()
        now = timezone.now()

        # Report
        report, _ = MobilizationReport.objects.get_or_create(
            title=scenario["report_title"],
            defaults={
                "community": community,
                "incident_type": incident_type,
                "severity": scenario["severity"],
                "status": "enacted",
                "summary": f"Automated ingress scenario: {scenario['report_title']}",
                "justification": "Generated for demo purposes.",
                "submitted_by": coordinator,
                "reviewed_by": coordinator,
                "enacted_by": coordinator,
                "enacted_at": now - timedelta(hours=random.randint(2, 48)),
            },
        )

        # Event
        event, _ = MobilizationEvent.objects.get_or_create(
            title=scenario["event_title"],
            defaults={
                "community": community,
                "source_report": report,
                "incident_type": incident_type,
                "status": scenario["event_status"],
                "coordinator": coordinator,
                "description": f"Response event created from report: {report.title}",
                "started_at": now - timedelta(hours=random.randint(1, 24)) if scenario["event_status"] == "active" else None,
            },
        )

        # Deployments
        for dep_data in scenario["deployments"]:
            dep, _ = Deployment.objects.get_or_create(
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

            # Assign some responders
            available = [r for r in responders if r.current_status in ("available", "standby")]
            for responder in random.sample(available, min(2, len(available))):
                DeploymentAssignment.objects.get_or_create(
                    deployment=dep,
                    responder=responder,
                    defaults={
                        "role": random.choice(["lead", "responder", "medic", "driver"]),
                        "status": "active" if dep_data["status"] == "active" else "assigned",
                    },
                )

            # Interventions
            for iv_data in dep_data.get("interventions", []):
                Intervention.objects.get_or_create(
                    deployment=dep,
                    title=iv_data["title"],
                    defaults={
                        "intervention_type": iv_data["intervention_type"],
                        "priority": iv_data["priority"],
                        "is_required": iv_data.get("is_required", True),
                        "status": random.choice(["todo", "in_progress"]) if dep_data["status"] == "active" else "todo",
                        "reported_by": coordinator,
                    },
                )

        print(f"  ✔ Scenario: {scenario['event_title']}")
