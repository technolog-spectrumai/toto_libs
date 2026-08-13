"""Seed the three plans.

**Seeding is what turns gating on**, and that is deliberate — the same rule
delta uses. `gate.is_entitled` answers True when no plan exists, so a host that
has never run this command is fully open, with the same code deployed. A bare
bring-up is a working platform; a full seed is a platform that sells something.

Unlike delta this runs at every level, not only `--full`. Delta's plans gate one
action and can be absent without anything looking odd; here the plans page is a
dashboard tile and an empty one would be a broken promise. What `--full` adds is
the demo community discount, which is data about a fixture rather than about the
platform.
"""

from toto.ingress import IngressCommand
from toto.subscriptions.models import CommunityPlanDiscount, SubscriptionPlan

#: The ladder. `units` is a QUANTITY, not a price — see subscriptions/services.py.
#: The numbers are round because the price of one unit is the dial an operator
#: actually turns; changing what Standard costs should not mean editing a seed.
PLANS = [
    {
        "code": "free",
        "name": "Free",
        "units": 0,
        "is_default": True,
        "order": 10,
        "description": (
            "Everything you need to be here: your profile, your files, your "
            "communities and your wallet. Costs nothing, and never expires."
        ),
        "entitlements": [],
    },
    {
        "code": "standard",
        "name": "Standard",
        "units": 200,
        "is_default": False,
        "order": 20,
        "description": (
            "The everyday tools — documents, sheets, drawings, tasks and maps. "
            "Metered work is still charged per use on top."
        ),
        "entitlements": [
            "cyprian", "memo", "primula", "sketch", "editor",
            "kanban", "polls", "locations",
        ],
    },
    {
        "code": "studio",
        "name": "Studio",
        "units": 600,
        "is_default": False,
        "order": 30,
        "description": (
            "Everything in Standard, plus the heavy end: PDF generation, "
            "workflows, notebooks, version control, media and signatures."
        ),
        "entitlements": [
            "cyprian", "memo", "primula", "sketch", "editor",
            "kanban", "polls", "locations",
            "aralia", "workflows", "mandragora", "notarius",
            "repo", "gitea", "vod", "portfolio", "jess", "travels",
        ],
    },
]


class Command(IngressCommand):
    help = "Seed the subscription plans."

    def process(self):
        created = 0
        for spec in PLANS:
            code = spec["code"]
            _, was_new = SubscriptionPlan.objects.update_or_create(
                code=code, defaults={k: v for k, v in spec.items() if k != "code"})
            created += int(was_new)
        self.stdout.write(
            f"subscriptions: {len(PLANS)} plans ensured ({created} new)")

        # Exactly one default, whatever a hand edit did in between. A second one
        # would make "what does somebody with no subscription get" ambiguous,
        # and `default_plan()` would answer it by primary key order.
        SubscriptionPlan.objects.exclude(code="free").filter(
            is_default=True).update(is_default=False)

        if not self.full:
            return

        # Demo data: a community that makes its members' Standard cheaper, so
        # the discount line on the plans page has something to say.
        self._seed_demo_discount()

    def _seed_demo_discount(self):
        from toto.socialhub.models import Community

        community = Community.objects.order_by("pk").first()
        plan = SubscriptionPlan.objects.filter(code="standard").first()
        if community is None or plan is None:
            self.stdout.write("subscriptions: no community to discount, skipped")
            return
        _, was_new = CommunityPlanDiscount.objects.update_or_create(
            community=community, plan=plan, defaults={"percent": 30})
        self.stdout.write(
            f"subscriptions: {community.name} → Standard −30% "
            f"({'created' if was_new else 'kept'})")
