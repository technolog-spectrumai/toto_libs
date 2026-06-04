import random
from datetime import timedelta

from django.utils import timezone

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community

from toto.magistrate.models import CommunityMagistrateSettings, Magistrate, MagistrateReport, MagistrateRole


ROLES = [
    {
        "name": "Prefect",
        "slug": "prefect",
        "description": "Head magistrate of a sub-community or chapter, responsible for local coordination, chapter governance, and urgent local directives.",
        "icon": "fa-solid fa-landmark-dome",
        "overseeing_logistics": True,
        "overseeing_interior": True,
        "order": 10,
    },
    {
        "name": "Tribune",
        "slug": "tribune",
        "description": "Protector of assembly procedure and civic rights; oversees tribunal access and legislative safeguards.",
        "icon": "fa-solid fa-scale-balanced",
        "overseeing_tribunal": True,
        "order": 20,
    },
    {
        "name": "Censor",
        "slug": "censor",
        "description": "Keeper of civic standards, education, eligibility records, and merchandise quality discipline.",
        "icon": "fa-solid fa-scroll",
        "overseeing_merchandise": True,
        "overseeing_education": True,
        "can_set_fines": True,
        "order": 30,
    },
    {
        "name": "Legate",
        "slug": "legate",
        "description": "Envoy-magistrate for inter-community relations, treaties, external missions, and chapter coordination.",
        "icon": "fa-solid fa-handshake",
        "overseeing_relations": True,
        "overseeing_logistics": True,
        "order": 40,
    },
    {
        "name": "Constable",
        "slug": "constable",
        "description": "Public order magistrate responsible for safety, internal movement, access control, and local enforcement.",
        "icon": "fa-solid fa-shield-halved",
        "overseeing_public_order": True,
        "overseeing_interior": True,
        "can_set_fines": True,
        "order": 50,
    },
    {
        "name": "Dux",
        "slug": "dux",
        "description": "Field commander for regional security, logistics, and emergency deployment.",
        "icon": "fa-solid fa-helmet-safety",
        "overseeing_public_order": True,
        "overseeing_logistics": True,
        "order": 60,
    },
    {
        "name": "Sacellarius",
        "slug": "sacellarius",
        "description": "Treasury auditor and comptroller overseeing accounts, productivity, fiscal discipline, and public funds.",
        "icon": "fa-solid fa-vault",
        "overseeing_finance": True,
        "overseeing_productivity": True,
        "can_set_fines": True,
        "order": 70,
    },
    {
        "name": "Questor",
        "slug": "questor",
        "description": "Tax and revenue magistrate responsible for dues, collections, trade levies, and fiscal obligations.",
        "icon": "fa-solid fa-coins",
        "overseeing_trade": True,
        "overseeing_finance": True,
        "can_set_fines": True,
        "order": 80,
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
        self._seed_community_settings(communities[:3])
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

    def _seed_community_settings(self, communities):
        """Create CommunityMagistrateSettings for each community, using the treasury account as collector."""
        try:
            from toto.assets.models import LedgerAccount
            treasury = LedgerAccount.objects.filter(code="treasury").first()
        except Exception:
            treasury = None

        for community in communities:
            _, created = CommunityMagistrateSettings.objects.get_or_create(
                community=community,
                defaults={
                    "max_fine_pct": 10,
                    "fine_collection_account": treasury,
                },
            )
            if created:
                acct_label = treasury.code if treasury else "none"
                print(f"  ✔  Magistrate settings for {community}: max 10%, collection → {acct_label}")
            else:
                print(f"  ·  Magistrate settings already exist for {community}")

    def _seed_roles(self):
        for r in ROLES:
            MagistrateRole.objects.update_or_create(
                slug=r["slug"],
                defaults={
                    "name": r["name"],
                    "icon": r["icon"],
                    "description": r["description"],
                    "overseeing_tribunal": r.get("overseeing_tribunal", False),
                    "overseeing_trade": r.get("overseeing_trade", False),
                    "overseeing_finance": r.get("overseeing_finance", False),
                    "overseeing_public_order": r.get("overseeing_public_order", False),
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
