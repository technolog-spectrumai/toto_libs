"""Seed Hesperis.

There is nothing to seed unconditionally: a bounty is a thing an operator
creates, not a fact of the installation. The whole command is therefore behind
``--fake-data``, which builds a small worked example so somebody can see the
board, the review queue and a published dataset without hand-building six
models in admin first.

**The flag is ``--full``, like every other app's.** It used to be a private
``--fake-data``, and the consequence was that this seeder was unreachable
through ``ingress_all`` at all: that command forwards ``full=settings.
FULL_INGRESS`` and nothing else, so no automated path and no operator running
``ingress_all --full`` could ever reach the body. The board stayed empty on a
host that had asked for demo data everywhere else.

Demo rows still cannot appear on a deployment that did not ask for them:
``FULL_INGRESS`` defaults to ``"0"``, the entrypoint runs a bare
``ingress_all``, and the deployer warns when the combination would seed. That
was always the property worth keeping — a private flag was simply the wrong way
to keep it, because it also locked out the people who wanted the data.

``--fake-data`` still works as an alias, so anything that typed it keeps
working.
"""

from django.core.management.base import CommandError
from django.utils import timezone

from toto.ingress import IngressCommand
from toto.kanban import work
from toto.kanban.models import (
    Campaign, ConsensusPolicy, Practitioner, Project, ProjectCommitment,
    Mission, ReviewVerdict, RewardPolicy, RewardTrigger, SubmissionResolution,
)
from toto.people.models import Person
from toto.hesperis import services
from toto.hesperis.models import Dataset, HesperisBounty, HesperisCampaign

#: The demo campaign's name is the idempotency key: re-running finds it and
#: stops, rather than growing a second copy of everything.
DEMO_CAMPAIGN = "Bridges of the Vistula"


class Command(IngressCommand):
    help = ("Seed Hesperis. Nothing runs without --full (or FULL_INGRESS=1), "
            "which builds two bounties, two reviewed contributions and two "
            "datasets.")

    def __init__(self):
        super().__init__()
        self.fake_data = False

    def add_arguments(self, parser):
        super().add_arguments(parser)
        # Kept as an alias for what this flag used to be called, so a script
        # or a habit that types it does not break. `--full` is the real one.
        parser.add_argument(
            "--fake-data",
            dest="fake_data",
            action="store_true",
            help="Alias for --full.",
        )

    def handle(self, *args, **options):
        self.fake_data = options.get("fake_data", False)
        super().handle(*args, **options)

    def process(self):
        # Either spelling. `self.full` is what `ingress_all` can actually set,
        # via settings.FULL_INGRESS; `fake_data` is the old name kept working.
        if not (self.full or self.fake_data):
            self.stdout.write(
                "Hesperis: nothing to seed. Pass --full (or set FULL_INGRESS=1) "
                "for a worked example.")
            return
        self._seed()

    # ── the worked example ───────────────────────────────────────────────────

    def _seed(self):
        people = list(Person.objects.all()[:4])
        if len(people) < 3:
            raise CommandError(
                "Hesperis fake data needs at least 3 Person rows (an author, a "
                "second author and a reviewer). Run the people ingress first.")

        if Campaign.objects.filter(name=DEMO_CAMPAIGN).exists():
            self.stdout.write(
                f"Hesperis: “{DEMO_CAMPAIGN}” already exists — nothing to do.")
            return

        lead, author_a, reviewer = people[0], people[1], people[2]
        author_b = people[3] if len(people) > 3 else author_a

        campaign, pcampaign = self._campaign(lead)
        self._make_reviewer(reviewer, campaign.project)
        bounties = self._bounties(campaign)
        self._reward(campaign)

        accepted = self._contributions(bounties, author_a, author_b, reviewer)
        self._datasets(pcampaign)

        self.stdout.write(self.style.SUCCESS(
            f"Hesperis: seeded “{DEMO_CAMPAIGN}” — "
            f"{len(bounties)} bounties, {accepted} accepted observation(s), "
            f"2 datasets. Open /hesperis/ to see the board."))
        self.stdout.write(
            "  Note: the reward policy pays in GEM. On a host with no GEM asset "
            "engraved, grants are recorded and marked failed/skipped with the "
            "reason — acceptance is unaffected, which is the intended split.")

    def _campaign(self, lead):
        project = Project.objects.create(
            name="Hesperis Demo",
            description="A worked example of crowdsourced collection.",
            project_lead=lead,
        )
        campaign = Campaign.objects.create(
            project=project,
            name=DEMO_CAMPAIGN,
            description="Photograph and describe the river crossings.",
            start_date=timezone.localdate(),
        )
        pcampaign = HesperisCampaign.objects.create(
            campaign=campaign,
            licence="CC BY-SA 4.0",
            attribution="Hesperis contributors",
            collection_scheme={
                "fields": [
                    {"name": "photo", "type": "file", "required": True},
                    {"name": "condition", "type": "choice",
                     "options": ["good", "worn", "damaged"]},
                ]
            },
        )
        return campaign, pcampaign

    @staticmethod
    def _make_reviewer(person, project):
        """A real reviewer, so the review queue is actually usable afterwards.

        Without an active reviewer Practitioner committed to the project,
        ``work.can_review`` refuses everybody and the queue renders empty —
        which would look like a broken page rather than a missing role.
        """
        practitioner, _ = Practitioner.objects.get_or_create(
            person=person,
            role=Practitioner.ROLE_REVIEWER,
            defaults={"is_active": True},
        )
        ProjectCommitment.objects.get_or_create(
            practitioner=practitioner, project=project,
            defaults={"hours_per_day": 4, "is_active": True},
        )
        return practitioner

    def _bounties(self, campaign):
        one_of_one = ConsensusPolicy.objects.filter(name="1 of 1").first()
        two_of_three = ConsensusPolicy.objects.filter(name="2 of 3").first()

        first = HesperisBounty.objects.create(
            mission=Mission.objects.create(
                campaign=campaign,
                title="Photograph the bridges",
                description="One clear photo of each crossing.",
                consensus_policy=one_of_one,
            ),
            instructions=(
                "Stand on the near bank and photograph the whole span. "
                "Note the condition of the deck and any closed lanes."),
            reward_summary="Gems per accepted photo",
        )
        second = HesperisBounty.objects.create(
            mission=Mission.objects.create(
                campaign=campaign,
                title="Map the riverside benches",
                description="Where can you sit by the water?",
                consensus_policy=two_of_three or one_of_one,
            ),
            instructions=(
                "Record each bench: where it is, whether it faces the water, "
                "and whether it is usable."),
            max_contributions=50,
        )
        return [first, second]

    @staticmethod
    def _reward(campaign):
        """Campaign-scoped, so both bounties inherit it.

        Paid in the first active ledger asset, if any — the same set the
        bounty form offers. Falls back to the symbol "GEM" on a host with no
        assets at all, so the demo still SHOWS a reward; settlement then fails
        with "No asset with symbol 'GEM'", which is the honest state of such a
        host and the thing the Distribute button reports.
        """
        from toto.assets.models import Asset  # noqa: PLC0415

        asset = Asset.objects.filter(active=True).order_by("pk").first()
        code = asset.unit_name if asset is not None else "GEM"
        for trigger, amount in (
            (RewardTrigger.SUBMISSION_ACCEPTED, 5),
            (RewardTrigger.REVIEW_RESOLVED, 1),
        ):
            RewardPolicy.objects.create(
                campaign=campaign,
                trigger=trigger,
                asset_code=code,
                amount_base_units=amount,
                funding_account_code="hesperis_demo_purse",
            )

    def _contributions(self, bounties, author_a, author_b, reviewer):
        """Three contributions showing all three outcomes: accepted, rejected, pending.

        Both REVIEWED ones go on the 1-of-1 bounty deliberately. The second
        bounty runs 2-of-3, so a single verdict there resolves nothing — the
        first draft of this seeder put the rejection on it and produced a
        submission stuck at "pending" that read as a broken reject path rather
        than as a rule waiting for its second reviewer.

        The unreviewed one goes on the 2-of-3 bounty, which is exactly what
        leaves something waiting in the review queue to click.
        """
        first, second = bounties
        accepted = 0

        # 1. accepted under 1-of-1, and therefore an observation
        one = work.submit(services.contribute(
            first, author_a,
            notes="Poniatowski bridge, south span. Deck sound, railings rusted."))
        work.record_review(one, reviewer, ReviewVerdict.ACCEPT, "Clear shot, usable.")
        one = work.resolve(one)
        if one.resolution == SubmissionResolution.ACCEPTED:
            services.accept(one)
            accepted += 1

        # 2. rejected under the same rule — the other outcome, and no observation
        two = work.submit(services.contribute(
            first, author_b,
            notes="A bridge, somewhere near the water I think."))
        work.record_review(
            two, reviewer, ReviewVerdict.REJECT, "No location given.")
        work.resolve(two)

        # 3. unreviewed under 2-of-3, so the review queue has something waiting
        work.submit(services.contribute(
            second, author_b,
            notes="Bench by the boulevards, faces the water, slats intact."))

        return accepted

    @staticmethod
    def _datasets(pcampaign):
        """Two datasets: one published, one not yet — both states visible."""
        bridges = Dataset.objects.create(
            campaign=pcampaign, name="Bridges", slug="bridges",
            description="Accepted photographs of the river crossings.",
            licence="CC BY-SA 4.0")
        Dataset.objects.create(
            campaign=pcampaign, name="Benches", slug="benches",
            description="Seating along the riverside. Not yet published.",
            licence="CC BY-SA 4.0")
        services.freeze(bridges, notes="First cut.")
