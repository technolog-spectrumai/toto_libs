"""Offer the plans to communities.

**The plans themselves are not seeded any more** — they live in plans.yaml and
are read into immutable objects at startup. What this command seeds is the
other half: WHICH COMMUNITIES MAY BUY WHICH PLAN, which is genuine state and
the only plan-shaped dial left in the database.

Eligibility is CLOSED BY DEFAULT: an offer table with no rows means nobody can
buy anything. So this always offers the default plan to every community, at
every level rather than only under ``--full`` — a bare bring-up must be a
working platform, and a plans page that shows a signed-in person nothing at
all is not one. ``--full`` additionally offers the paid ladder to the first
community, so a demo has something to sell, and keeps the demo discount.

It also VALIDATES the file and fails loudly on a bad one, which makes
``ingress_all`` the deploy-time leg of the same check ``manage.py check``
performs at build time.

(This used to seed three SubscriptionPlan rows and carry a `RENAMED` pre-pass
that renamed `studio` to `professional` in place so its subscribers survived.
Both went with the table on 2026-09-02: a rename is an edit to plans.yaml now,
and carrying subscribers across one is a `plan_key` rewrite rather than a row
mutation. The sentence "seeding is what turns gating on" went with them too —
the ladder is never empty, so enforcement is the flag's decision alone.)
"""

from django.core.management.base import CommandError

from toto.ingress import IngressCommand
from toto.subscriptions import plans
from toto.subscriptions.models import CommunityDiscount, CommunityPlanOffer


class Command(IngressCommand):
    help = "Offer the plans to communities (the plans themselves are in plans.yaml)."

    def process(self):
        problems = plans.validate()
        if problems:
            raise CommandError(
                "plans.yaml is not usable:\n  - " + "\n  - ".join(problems))

        from django.core.management import call_command

        from toto.socialhub.models import Community

        # The operators' Community and the superusers' plan, every run
        # (bootstrap_plans is idempotent): a host is never left with admins
        # on Free because nobody ran the command by hand.
        call_command("bootstrap_plans", stdout=self.stdout)
        if self.full:
            self._seed_demo_communities()

        communities = list(Community.objects.order_by("pk"))
        if not communities:
            self.stdout.write("subscriptions: no communities yet, nothing to offer")
            return

        default = plans.default_plan()
        created = 0
        for community in communities:
            _row, was_new = CommunityPlanOffer.objects.get_or_create(
                community=community, plan_key=default.key)
            created += int(was_new)
        self.stdout.write(
            f"subscriptions: '{default.key}' offered to {len(communities)} "
            f"community/ies ({created} new)")

        if not self.full:
            return

        # Demo data: the first community gets the paid ladder and a discount,
        # so the plans page has something to sell and the discount line has
        # something to say.
        first = communities[0]
        paid = [plan for plan in plans.all_plans() if not plan.is_default]
        for plan in paid:
            CommunityPlanOffer.objects.get_or_create(
                community=first, plan_key=plan.key)
        self.stdout.write(
            f"subscriptions: {first.name} offered "
            f"{', '.join(plan.key for plan in paid) or 'nothing else'}")
        self._seed_demo_discount(first)

    def _seed_demo_communities(self):
        """Three Communities that make eligibility observable in a demo and in
        tests: `toto` (a company) with the child `toto-dev`, which alone is
        offered Standard and Developer — a member of toto-dev may buy them, a
        member of toto only may not — and `quiet-harbour`, offered nothing but
        the default (the isolated case)."""
        from toto.socialhub.models import Community

        toto, _ = Community.objects.get_or_create(
            slug="toto", defaults={"name": "toto", "org_type": "company"})
        dev, _ = Community.objects.get_or_create(
            slug="toto-dev", defaults={"name": "toto-dev", "org_type": "company", "parent": toto})
        Community.objects.get_or_create(slug="quiet-harbour", defaults={"name": "Quiet Harbour"})
        for key in ("standard", "developer"):
            if plans.get(key) is not None:
                CommunityPlanOffer.objects.get_or_create(community=dev, plan_key=key)
        self.stdout.write("subscriptions: toto → toto-dev (standard, developer) and Quiet Harbour seeded")

    def _seed_demo_discount(self, community):
        _, was_new = CommunityDiscount.objects.update_or_create(
            community=community, defaults={"percent": 30})
        self.stdout.write(
            f"subscriptions: {community.name} −30% on any plan "
            f"({'created' if was_new else 'kept'})")
