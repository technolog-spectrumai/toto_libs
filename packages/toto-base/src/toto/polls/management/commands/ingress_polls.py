"""Seed something for every tab: a poll, a vote, a quiz and an electorate.

The poll/vote pair is the point of the first two — the poll is revisable with
a live count, the vote is cast once and sealed until it closes. Seeding only a
poll would leave the Votes tab looking broken on a fresh install, and the same
is true of the Quizzes tab, which is why it is seeded here
too.

The electorate is seeded from a real community's membership — the demo company
socialhub's ingress creates — because that is the whole shape of governance
after the Business Center went away: a company is a community, its register is
its membership, and weights are DATA an operator edits rather than anything
derived from a cap table.
"""

import random

from django.contrib.auth import get_user_model
from django.utils import timezone

from toto.ingress import IngressCommand
from toto.polls.core import Revisability, Visibility
from toto.polls.models import (SCOPE_COMMUNITY, Ballot, Choice, Kind, Question)
from toto.polls.quiz_models import Quiz, QuizAnswer, QuizQuestion

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

    def process(self):
        # Nothing is seeded unconditionally any more. The consensus profiles
        # and quorum rules that used to be created on every ingress were the
        # machinery of formal votes, and formal votes are Irena's since 1.50.
        if not self.full:
            return

        self._seed_quiz()

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

        # A consultation with the OTHER pair of settings — one answer each and
        # no running count — to show that both shapes are still available.
        # It is not a formal vote and does not decide anything: it asks.
        sealed = Question.objects.create(
            kind=Kind.POLL,
            title="Should we adopt the proposed code of conduct?",
            question_text="What does the community think?",
            body=("The full text was circulated a week before this poll "
                  "opened. This is a consultation, not a formal vote: the "
                  "result is advisory."),
            revisability=Revisability.FINAL,
            visibility=Visibility.ON_CLOSE,
            closes_at=timezone.now() + timezone.timedelta(days=7),
            created_by=users[0],
        )
        for position, label in enumerate(["Yes", "No", "No opinion"]):
            Choice.objects.create(question=sealed, label=label, position=position)

        poll_choices = list(poll.choices.all())
        for user in users:
            Ballot.objects.get_or_create(
                question=poll, voter=user,
                defaults={"choice": random.choice(poll_choices)})

        print(f"[Ingress] Seeded 2 polls for {len(users)} user(s).")

    # ---- the Quizzes tab -------------------------------------------------

    #: (question, multi?, [(answer, correct?)])
    QUIZ_QUESTIONS = [
        ("What makes a formal vote different from a poll?", False, [
            ("A ballot is final once cast", True),
            ("It has more options", False),
            ("Only staff may see it", False),
        ]),
        ("Which of these are frozen when a vote opens?", True, [
            ("The register of who may vote", True),
            ("The consensus rule and its percentage", True),
            ("The number of ballots cast", False),
        ]),
        ("A decision's hash covers its own payload and what else?", False, [
            ("The hash of the decision before it", True),
            ("The voter's password", False),
            ("Nothing else", False),
        ]),
    ]

    def _seed_quiz(self):
        """One quiz, so the tab has something real to open.

        Completion-only (`pass_mark=None`) and unlimited attempts: the gentler
        of the two shapes, and the one whose certificate says "completed"
        rather than judging anybody on demo data.
        """
        if Quiz.objects.filter(slug="how-voting-works").exists():
            print("[Ingress] Quiz demo already exists — skipping.")
            return

        author = User.objects.order_by("pk").first()
        quiz = Quiz.objects.create(
            title="How voting works here",
            slug="how-voting-works",
            description=("A short tour of the rules this platform enforces: "
                         "what a formal vote freezes, and what a decision's "
                         "hash actually covers."),
            pass_mark=None,
            max_attempts=None,
            created_by=author,
        )
        for position, (text, multi, answers) in enumerate(self.QUIZ_QUESTIONS):
            question = QuizQuestion.objects.create(
                quiz=quiz, text=text, is_multiple_choice=multi,
                position=position)
            for answer_position, (answer_text, correct) in enumerate(answers):
                QuizAnswer.objects.create(
                    question=question, text=answer_text, is_correct=correct,
                    position=answer_position)

        print(f"[Ingress] Seeded 1 quiz with {len(self.QUIZ_QUESTIONS)} questions.")


