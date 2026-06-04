import random
from datetime import timedelta

from django.utils import timezone

from toto.ingress import IngressCommand
from toto.people.models import Person
from toto.socialhub.models import Community

from toto.assembly.models import (
    AssemblyProposal,
    AssemblyProposalType,
    AssemblyStatus,
    AssemblyVote,
    AssemblyVoteChoice,
    CommunityAssemblyConfig,
    CommunityRule,
    CommunitySenate,
)


RULES = [
    (
        "Respectful Discourse",
        "All members must engage with one another respectfully. Personal attacks, harassment, "
        "and intimidation are grounds for tribunal referral.",
    ),
    (
        "Quorum for Major Decisions",
        "Proposals amending community rules or levying new taxes require a minimum of 20% member "
        "participation before the result is binding.",
    ),
    (
        "Transparency in Trade",
        "Any member acting as an agent in a trade on behalf of others must disclose their principal "
        "relationship before the transaction is completed.",
    ),
    (
        "Emergency Powers Limitation",
        "Emergency declarations may not remain active for more than 30 days without a ratifying "
        "assembly vote. Expired declarations lapse automatically.",
    ),
    (
        "No Retroactive Penalties",
        "No fine, tax, or sanction may be applied retroactively. All penalties must refer to "
        "conduct occurring after the relevant rule was enacted.",
    ),
]

PROPOSAL_SAMPLES = [
    {
        "proposal_type": AssemblyProposalType.RULE,
        "title": "Amend: Extend Emergency Powers Cap to 60 Days",
        "body": (
            "The current 30-day cap on emergency declarations is insufficient for extended "
            "community crises. This proposal extends the maximum to 60 days, still requiring "
            "a ratifying vote thereafter."
        ),
        "status": AssemblyStatus.OPEN,
    },
    {
        "proposal_type": AssemblyProposalType.RULE,
        "title": "New Rule: Mandatory Conflict-of-Interest Disclosure",
        "body": (
            "Any magistrate or assembly member who has a financial stake in a proposal's "
            "outcome must declare the conflict before voting or deliberation."
        ),
        "status": AssemblyStatus.OPEN,
    },
    {
        "proposal_type": AssemblyProposalType.ASSET_TAX,
        "title": "Reduce Blanket Transaction Fee by 20 bps",
        "body": (
            "Community transaction fees are currently impeding small-value trades. "
            "This proposal reduces the blanket fee from the current rate by 20 basis points."
        ),
        "status": AssemblyStatus.PASSED,
    },
    {
        "proposal_type": AssemblyProposalType.RULE,
        "title": "Repeal: Quorum for Major Decisions (replace with 15%)",
        "body": (
            "The 20% quorum threshold is routinely missed and renders decisions invalid. "
            "This repeal replaces it with a new 15% threshold via a companion rule."
        ),
        "status": AssemblyStatus.REJECTED,
        "is_repeal": True,
    },
    {
        "proposal_type": AssemblyProposalType.EMERGENCY_DECLARATION,
        "title": "Emergency: Suspend External Trade Pending Security Review",
        "body": (
            "Following reports of fraudulent counterparties, this declaration suspends all "
            "external trades for 14 days while the Quaestor completes a security audit."
        ),
        "status": AssemblyStatus.PASSED,
    },
    {
        "proposal_type": AssemblyProposalType.RULE,
        "title": "New Rule: Mandatory Cooling-Off Period Between Proposals",
        "body": (
            "A member who opens a proposal that is rejected may not open another proposal "
            "of the same type for 30 days. Prevents proposal flooding."
        ),
        "status": AssemblyStatus.PENDING_SENATE,
    },
]


class Command(IngressCommand):
    help = "Seeds assembly configs, rules, proposals, votes, and an optional senate for demo communities"

    def process(self):
        communities = list(Community.objects.all())
        if not communities:
            print("⚠  No communities — run socialhub ingress first.")
            return

        persons = list(Person.objects.all())
        if not persons:
            print("⚠  No persons — run people ingress first.")
            return

        for community in communities[:3]:
            self._seed_config(community)
            self._seed_rules(community)
            self._seed_proposals(community, persons)

        self._seed_senate(communities[0], persons)

        print(
            f"✔  Assembly ingress complete: "
            f"{CommunityAssemblyConfig.objects.count()} configs, "
            f"{CommunityRule.objects.count()} rules, "
            f"{AssemblyProposal.objects.count()} proposals."
        )

    def _seed_config(self, community):
        _, created = CommunityAssemblyConfig.objects.get_or_create(
            community=community,
            defaults={"quorum_fraction": "0.5100"},
        )
        if created:
            print(f"  ✔  Assembly config for {community} (51% quorum)")
        else:
            print(f"  ·  Config already exists for {community}")

    def _seed_rules(self, community):
        if CommunityRule.objects.filter(community=community).exists():
            print(f"  ·  Rules already exist for {community}")
            return
        for title, text in RULES:
            CommunityRule.objects.create(community=community, title=title, text=text, active=True)
        print(f"  ✔  {len(RULES)} rules seeded for {community}")

    def _seed_proposals(self, community, persons):
        if AssemblyProposal.objects.filter(community=community).exists():
            print(f"  ·  Proposals already exist for {community}")
            return

        now = timezone.now()
        voters = random.sample(persons, min(6, len(persons)))

        for sample in PROPOSAL_SAMPLES:
            opens_at = now - timedelta(days=random.randint(3, 14))
            closes_at = opens_at + timedelta(days=7)

            kwargs = {
                "community": community,
                "proposal_type": sample["proposal_type"],
                "title": sample["title"],
                "body": sample.get("body", ""),
                "status": sample["status"],
                "is_repeal": sample.get("is_repeal", False),
                "opened_by": random.choice(persons),
                "opens_at": opens_at,
                "closes_at": closes_at if sample["status"] != AssemblyStatus.OPEN else now + timedelta(days=3),
            }

            if sample["status"] == AssemblyStatus.PENDING_SENATE:
                kwargs["senate_deadline"] = now + timedelta(days=5)

            proposal = AssemblyProposal.objects.create(**kwargs)
            self._seed_votes(proposal, voters)

        print(f"  ✔  {len(PROPOSAL_SAMPLES)} proposals seeded for {community}")

    def _seed_votes(self, proposal, voters):
        if proposal.status == AssemblyStatus.OPEN:
            # Only some members have voted yet
            panel = random.sample(voters, random.randint(1, max(1, len(voters) // 2)))
        else:
            panel = voters

        for person in panel:
            if proposal.status in (AssemblyStatus.PASSED, AssemblyStatus.PENDING_SENATE):
                vote = random.choices(
                    [AssemblyVoteChoice.YES, AssemblyVoteChoice.NO, AssemblyVoteChoice.ABSTAIN],
                    weights=[6, 2, 1],
                )[0]
            elif proposal.status == AssemblyStatus.REJECTED:
                vote = random.choices(
                    [AssemblyVoteChoice.YES, AssemblyVoteChoice.NO, AssemblyVoteChoice.ABSTAIN],
                    weights=[2, 6, 1],
                )[0]
            else:
                vote = random.choice(list(AssemblyVoteChoice.values))

            AssemblyVote.objects.get_or_create(
                proposal=proposal,
                voter=person,
                defaults={"vote": vote},
            )

    def _seed_senate(self, community, persons):
        senate, created = CommunitySenate.objects.get_or_create(
            community=community,
            defaults={"is_active": True, "veto_window_days": 7},
        )
        if not created:
            print(f"  ·  Senate already exists for {community}")
            return

        # Add 2–3 senators (distinct from typical assembly voters in spirit, but same pool for demo)
        senators = random.sample(persons, min(3, len(persons)))
        senate.members.set(senators)
        names = ", ".join(str(p) for p in senators)
        print(f"  ✔  Senate seeded for {community}: {names}")
