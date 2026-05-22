import random
from datetime import timedelta

from django.utils import timezone

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community

from toto.magistrate.models import Magistrate, MagistrateReport, MagistrateRole


ROLES = [
    {
        "name": "Prefect",
        "slug": "prefect",
        "icon": "fa-solid fa-star",
        "description": (
            "Chief executive magistrate. Oversees mobilization, public order, and legislation. "
            "May act on emergency declarations without a full assembly vote."
        ),
        "overseeing_mobilization": True,
        "overseeing_public_order": True,
        "overseeing_legislation": True,
        "order": 1,
    },
    {
        "name": "Tribune",
        "slug": "tribune",
        "icon": "fa-solid fa-scale-balanced",
        "description": (
            "Protector of community rights. Oversees the tribunal and may veto "
            "decisions that violate community rules."
        ),
        "overseeing_tribunal": True,
        "overseeing_legislation": True,
        "order": 2,
    },
    {
        "name": "Legate",
        "slug": "legate",
        "icon": "fa-solid fa-shield-halved",
        "description": (
            "Field commander and liaison. Oversees mobilization operations and "
            "coordinates inter-community response."
        ),
        "overseeing_mobilization": True,
        "overseeing_public_order": True,
        "order": 3,
    },
    {
        "name": "Quaestor",
        "slug": "quaestor",
        "icon": "fa-solid fa-coins",
        "description": (
            "Financial officer. Oversees community finances, taxation, and trade. "
            "Reports treasury status to the assembly each quarter."
        ),
        "overseeing_finance": True,
        "overseeing_trade": True,
        "order": 4,
    },
    {
        "name": "Aedile",
        "slug": "aedile",
        "icon": "fa-solid fa-building-columns",
        "description": (
            "Public works and order magistrate. Oversees infrastructure, public spaces, "
            "and local commerce."
        ),
        "overseeing_trade": True,
        "overseeing_public_order": True,
        "order": 5,
    },
]

REPORT_TEMPLATES = [
    (
        "Q1 Activity Report",
        (
            "During this reporting period the following actions were taken: "
            "emergency protocols reviewed and updated; field coordination meetings held weekly; "
            "three community welfare initiatives advanced to deployment stage."
        ),
    ),
    (
        "Monthly Status Report",
        (
            "All assigned duties carried out in accordance with the community mandate. "
            "No major incidents requiring extraordinary measures. "
            "Coordination with adjacent offices maintained."
        ),
    ),
    (
        "Emergency Operations Summary",
        (
            "Summary of actions taken during the active emergency period. "
            "Mobilization orders issued under magistrate authority. "
            "Four deployments activated; two completed; two ongoing. "
            "Full debrief to follow."
        ),
    ),
]


class Command(IngressCommand):
    help = "Seeds magistrate roles and demo magistrate seats with assembly election context"

    def process(self):
        self._seed_roles()

        communities = list(Community.objects.all())
        if not communities:
            print("⚠  No communities — run socialhub ingress first.")
            return

        federal_agents = list(
            Person.objects.filter(is_federal_agent=True).order_by("?")[:10]
        )
        if not federal_agents:
            print("⚠  No federal agents — run people/mobilization ingress first.")
            return

        roles = list(MagistrateRole.objects.all())
        created = 0

        for community in communities[:3]:
            for role in random.sample(roles, min(3, len(roles))):
                person = random.choice(federal_agents)
                today = timezone.now().date()
                term_months = random.choice([6, 12, 18])

                mag, mag_created = Magistrate.objects.get_or_create(
                    person=person,
                    role=role,
                    community=community,
                    defaults={
                        "status": random.choice(["active", "active", "active", "term_ended"]),
                        "term_start": today - timedelta(days=random.randint(30, 180)),
                        "term_end": today + timedelta(days=30 * term_months),
                        "elected_at": timezone.now() - timedelta(days=random.randint(10, 120)),
                        "notes": f"Elected to serve as {role.name} by community vote.",
                    },
                )
                if mag_created:
                    created += 1
                    self._seed_reports(mag)

        print(f"✔  Magistrate ingress complete: {MagistrateRole.objects.count()} roles, {created} new seats.")

    def _seed_roles(self):
        for r in ROLES:
            MagistrateRole.objects.update_or_create(
                slug=r["slug"],
                defaults={
                    "name": r["name"],
                    "icon": r["icon"],
                    "description": r["description"],
                    "overseeing_mobilization": r.get("overseeing_mobilization", False),
                    "overseeing_tribunal": r.get("overseeing_tribunal", False),
                    "overseeing_trade": r.get("overseeing_trade", False),
                    "overseeing_finance": r.get("overseeing_finance", False),
                    "overseeing_public_order": r.get("overseeing_public_order", False),
                    "overseeing_legislation": r.get("overseeing_legislation", False),
                    "order": r["order"],
                },
            )
        print(f"✔  Magistrate roles: {MagistrateRole.objects.count()} total.")

    def _seed_reports(self, mag: Magistrate):
        n = random.randint(0, 2)
        for i, (title, body) in enumerate(random.sample(REPORT_TEMPLATES, min(n, len(REPORT_TEMPLATES)))):
            start = (timezone.now() - timedelta(days=90 * (i + 1))).date()
            end = (timezone.now() - timedelta(days=30 * i)).date()
            status = random.choice(["submitted", "acknowledged"])
            MagistrateReport.objects.get_or_create(
                magistrate=mag,
                title=title,
                defaults={
                    "body": body,
                    "reporting_period_start": start,
                    "reporting_period_end": end,
                    "status": status,
                    "submitted_at": timezone.now() - timedelta(days=random.randint(5, 60)),
                    "acknowledged_at": timezone.now() - timedelta(days=random.randint(1, 10)) if status == "acknowledged" else None,
                },
            )
