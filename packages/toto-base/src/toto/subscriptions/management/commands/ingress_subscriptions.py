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
from toto.subscriptions.models import CommunityDiscount, SubscriptionPlan

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
            "The everyday tools — text and code, tasks and maps. Read "
            "and export documents, sheets and decks; writing them is "
            "Professional. Metered work is still charged per use on top."
        ),
        # `cyprian`, `memo` and `primula` are deliberately NOT here (8/2026).
        # The three rich editors are Professional, and the gate is what makes
        # that a partial rather than a total exclusion: it refuses unsafe
        # methods only, so a Standard subscriber still opens, reads, prints and
        # downloads every document, deck and workbook they can see — they just
        # cannot save one. That is the "read-only viewing of paid apps" property
        # gate.py describes, and it is the reason this list can shrink without
        # anybody losing access to their own files.
        #
        # `editor` stays: the ACE editor is text and code, it is not metered,
        # and taking plain-text editing away from Standard would be a different
        # decision than the one made here.
        # `polls` is deliberately absent from BOTH plans, and was removed from
        # the catalogue with it. Polls became a Forum feature, the gate reads
        # the entitlement off the URL namespace, and `forum` is free — so the
        # row was selling something nobody could be charged for. Room polls
        # already resolved as `forum` before this; removing it wrote down what
        # was already true rather than changing what anybody pays.
        "entitlements": [
            "editor",
            "kanban", "locations",
        ],
    },
    {
        "code": "professional",
        "name": "Professional",
        "units": 600,
        "is_default": False,
        "order": 30,
        "description": (
            "Everything in Standard, plus the writing tools — documents, "
            "sheets and presentations — and the heavy end: PDF generation, "
            "notebooks, version control, media, signatures and the assistant."
        ),
        # Functionality only — workflows and email are machinery and belong
        # to every plan (see catalogue.py: plans differ by functionality,
        # not internals; everyone gets celery and workers).
        #
        # The compute tier is listed WITH `anastasia`, never without it.
        # catalogue.py states the rule: "a plan that included the labs but not
        # the capacity to run them would sell four buttons that all refuse."
        # It was missing entirely until now, so every one of these was declared
        # as paid in the catalogue and granted by no plan — which hid their
        # dashboard tiles from everybody, superusers included, and 402'd their
        # write endpoints wherever BUILD_SUBSCRIPTIONS_ENFORCE was on. The
        # description above has promised "notebooks" and "media" the whole time.
        "entitlements": [
            "cyprian", "memo", "primula", "editor",
            "kanban", "locations",
            "aralia", "mandragora", "notarius",
            "repo", "gitea", "vod", "steven", "travels",
            "anastasia", "dracena", "texlab", "manta",
        ],
    },
]

#: Plans that changed code. update_or_create keys on code, so without this a
#: rename would leave the old row behind as an active orphan while its
#: subscribers silently stop matching the seeded ladder.
RENAMED = {"studio": "professional"}


class Command(IngressCommand):
    help = "Seed the subscription plans."

    def process(self):
        self._apply_renames()
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

        # Demo data: a community that makes its members' subscription cheaper,
        # so the discount line on the plans page has something to say.
        self._seed_demo_discount()

    def _apply_renames(self):
        """Carry a renamed plan's subscribers over instead of orphaning them.

        Rename in place when only the old code exists — every Subscription FK
        keeps its row. When both exist (an operator already made the new one),
        the old row is deactivated instead: never merge two plans by guess.
        """
        for old, new in RENAMED.items():
            old_row = SubscriptionPlan.objects.filter(code=old).first()
            if old_row is None:
                continue
            if SubscriptionPlan.objects.filter(code=new).exists():
                if old_row.active:
                    old_row.active = False
                    old_row.save(update_fields=["active"])
                    self.stdout.write(
                        f"subscriptions: '{old}' deactivated "
                        f"('{new}' already exists)")
                continue
            old_row.code = new
            old_row.save(update_fields=["code"])
            self.stdout.write(f"subscriptions: '{old}' renamed to '{new}'")

    def _seed_demo_discount(self):
        from toto.socialhub.models import Community

        community = Community.objects.order_by("pk").first()
        if community is None:
            self.stdout.write("subscriptions: no community to discount, skipped")
            return
        _, was_new = CommunityDiscount.objects.update_or_create(
            community=community, defaults={"percent": 30})
        self.stdout.write(
            f"subscriptions: {community.name} −30% on any plan "
            f"({'created' if was_new else 'kept'})")
