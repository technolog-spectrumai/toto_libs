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
            "May act on emergency declarations without a full assembly vote. "
            "May levy infraction fines as the senior executive authority."
        ),
        "overseeing_mobilization": True,
        "overseeing_public_order": True,
        "overseeing_legislation": True,
        "can_set_fines": True,
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
            "May order trade reversals (with mandatory fine) and reports treasury status to the assembly each quarter."
        ),
        "overseeing_finance": True,
        "overseeing_trade": True,
        "can_set_fines": True,
        "order": 4,
    },
    {
        "name": "Aedile",
        "slug": "aedile",
        "icon": "fa-solid fa-building-columns",
        "description": (
            "Public works and market magistrate. Oversees infrastructure, public spaces, "
            "and local commerce. May inspect merchandise quality and impose fines contestable before the tribunal."
        ),
        "overseeing_trade": True,
        "overseeing_public_order": True,
        "overseeing_merchandise": True,
        "can_set_fines": True,
        "order": 5,
    },
    {
        "name": "Censor",
        "slug": "censor",
        "icon": "fa-solid fa-graduation-cap",
        "description": (
            "Overseer of education and civic standards. Maintains the quality of community academies, "
            "knowledge programmes, and may fast-track educational legislation."
        ),
        "overseeing_education": True,
        "overseeing_legislation": True,
        "order": 6,
    },
    {
        "name": "Propraetor",
        "slug": "propraetor",
        "icon": "fa-solid fa-handshake",
        "description": (
            "Diplomatic magistrate. Oversees inter-community relations, external treaties, "
            "and liaison with other communities on behalf of the assembly."
        ),
        "overseeing_relations": True,
        "order": 7,
    },
    {
        "name": "Curator",
        "slug": "curator",
        "icon": "fa-solid fa-truck-fast",
        "description": (
            "Supply and logistics magistrate. Oversees transport operations, supply chains, "
            "and the flow of goods across community routes."
        ),
        "overseeing_logistics": True,
        "overseeing_public_order": True,
        "order": 8,
    },
    {
        "name": "Praetor",
        "slug": "praetor",
        "icon": "fa-solid fa-compass",
        "description": (
            "Interior magistrate. Oversees internal travel, location access, and the "
            "movement of persons within community territory. May restrict or permit transit through controlled zones."
        ),
        "overseeing_interior": True,
        "overseeing_public_order": True,
        "order": 9,
    },
    {
        "name": "Procurator",
        "slug": "procurator",
        "icon": "fa-solid fa-briefcase",
        "description": (
            "Productivity magistrate. Oversees community work assignments, labour standards, "
            "and output compliance. May set mandatory productivity targets and review work records."
        ),
        "overseeing_productivity": True,
        "order": 10,
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

        self._seed_founder_prefect()
        print(f"✔  Magistrate ingress complete: {MagistrateRole.objects.count()} roles, {created} new seats.")

    def _seed_founder_prefect(self):
        """Assign the admin user's Person (Founder) as Prefect in the first community — for testing."""
        from django.contrib.auth import get_user_model
        User = get_user_model()
        admin_user = User.objects.filter(is_superuser=True).order_by("id").first()
        if not admin_user:
            return

        founder = Person.objects.filter(user=admin_user).first()
        if not founder:
            # Try by common names
            founder = Person.objects.filter(display_name__icontains="founder").first()
        if not founder:
            print("  ⚠  No founder/admin Person found for Prefect seed.")
            return

        if not founder.is_federal_agent:
            founder.is_federal_agent = True
            founder.save(update_fields=["is_federal_agent"])

        prefect_role = MagistrateRole.objects.filter(slug="prefect").first()
        community = Community.objects.first()
        if not prefect_role or not community:
            return

        today = timezone.now().date()
        mag, created = Magistrate.objects.get_or_create(
            person=founder,
            role=prefect_role,
            community=community,
            defaults={
                "status": "active",
                "term_start": today,
                "term_end": today + timedelta(days=365),
                "elected_at": timezone.now(),
                "notes": "Seeded by ingress for testing — founder Prefect seat.",
            },
        )
        if created:
            print(f"  ✔  {founder} seated as Prefect in {community} (testing seat).")
        else:
            print(f"  ·  Founder Prefect seat already exists.")

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
                    "overseeing_merchandise": r.get("overseeing_merchandise", False),
                    "overseeing_education": r.get("overseeing_education", False),
                    "overseeing_relations": r.get("overseeing_relations", False),
                    "overseeing_logistics": r.get("overseeing_logistics", False),
                    "overseeing_interior": r.get("overseeing_interior", False),
                    "overseeing_productivity": r.get("overseeing_productivity", False),
                    "can_set_fines": r.get("can_set_fines", False),
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
