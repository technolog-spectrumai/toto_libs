"""Seed one poll and one vote, so both tabs have something to show.

Two questions rather than one, because the pair is the point: the poll is
revisable with a live count, the vote is cast once and sealed until it closes.
Seeding only a poll would leave the Votes tab looking broken on a fresh
install.
"""

import random

from django.contrib.auth import get_user_model
from django.utils import timezone

from toto.ingress import IngressCommand
from toto.polls.core import Revisability, Visibility
from toto.polls.models import Ballot, Choice, Kind, Question

User = get_user_model()


class Command(IngressCommand):
    help = "Seeds a demo poll and a demo formal vote."

    #: The named thresholds every deployment gets. Seeded always, not only
    #: with --full: a vote cannot select a rule that does not exist, and a
    #: fresh platform having no rules at all is a broken feature rather than
    #: missing demo data.
    PROFILES = [
        ("Normal Majority", "50.00",
         "More than half of the decided weight."),
        ("Strong Majority", "60.00",
         "More than three fifths of the decided weight."),
        ("Supermajority", "75.00",
         "More than three quarters of the decided weight."),
        ("Near Unanimity", "80.00",
         "More than four fifths of the decided weight."),
    ]

    def _seed_profiles(self):
        from decimal import Decimal

        from toto.polls.models import ConsensusProfile

        for name, percent, description in self.PROFILES:
            ConsensusProfile.objects.get_or_create(
                name=name,
                defaults={"percent": Decimal(percent),
                          "description": description})

    QUORUM_RULES = [
        ("No additional quorum", "none", None,
         "The session decides with whoever attends."),
        ("Half the voting weight", "percent", "50.00",
         "At least half of the electorate weight must be represented."),
        ("Manual confirmation", "manual", None,
         "The chair confirms quorum on the record."),
    ]

    def _seed_quorum_rules(self):
        from decimal import Decimal

        from toto.polls.models import QuorumRule

        for name, mode, threshold, description in self.QUORUM_RULES:
            QuorumRule.objects.get_or_create(
                name=name,
                defaults={"mode": mode,
                          "threshold": (Decimal(threshold)
                                        if threshold else None),
                          "description": description})

    def process(self):
        self._seed_profiles()
        self._seed_quorum_rules()
        if not self.full:
            return

        if Question.objects.filter(slug="favourite-colour").exists():
            print("[Ingress] Polls demo already exists — skipping.")
            return

        users = list(User.objects.all())
        if not users:
            raise Exception("❌ Need at least 1 User to seed ballots.")

        poll = Question.objects.create(
            kind=Kind.POLL,
            title="Favourite colour",
            question_text="Which colour should the next release ship with?",
            revisability=Revisability.OPEN,
            visibility=Visibility.LIVE,
            created_by=users[0],
        )
        colours = ["Indigo", "Emerald", "Amber", "Crimson"]
        for position, label in enumerate(colours):
            Choice.objects.create(question=poll, label=label, position=position)

        vote = Question.objects.create(
            kind=Kind.VOTE,
            title="Adopt the code of conduct",
            question_text="Do you adopt the proposed code of conduct?",
            body=("The full text was circulated a week before this vote "
                  "opened. A ballot is final once cast."),
            # A formal vote: one ballot each, and no running tally.
            revisability=Revisability.FINAL,
            visibility=Visibility.ON_CLOSE,
            closes_at=timezone.now() + timezone.timedelta(days=7),
            created_by=users[0],
        )
        for position, label in enumerate(["Yes", "No", "Abstain"]):
            Choice.objects.create(question=vote, label=label, position=position)

        poll_choices = list(poll.choices.all())
        for user in users:
            Ballot.objects.get_or_create(
                question=poll, voter=user,
                defaults={"choice": random.choice(poll_choices)})

        print(f"[Ingress] Seeded 1 poll and 1 vote for {len(users)} user(s).")
