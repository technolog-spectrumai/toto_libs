"""Recording an answer and counting them. The only door into either.

Every consumer — the Polls pages and the Forum room — goes through
:func:`cast`. That is the point: closed means closed and one ballot per person
are worth nothing if three call sites each implement them, because the third
one will get it wrong and nobody will notice until somebody disputes a number.

The old app's voting was ``Vote.objects.update_or_create(...)`` inline in a
view, which silently made every ballot editable forever.

**A consultation, since 1.50.** This module used to carry the other half of a
governance engine: resolving an electorate from a registry, freezing a roll,
snapshotting quorum and consensus rules, evaluating them, excluding voters,
recording an immutable chain-linked Decision, and rendering paper results.
Twenty-four functions of it. All of that is Irena's now — it is the register of
record for company governance — and Ireneo presents verified copies. What is
left counts responses to a question and says so.

The electorate seam survives as exactly one implementation, `OpenToAll`:
everyone signed in, one vote each. It is kept as a seam rather than inlined
because `toto.forum` passes an electorate explicitly, and because "who may
answer" is a real question even when today's answer is "anyone who may see it".
"""

from __future__ import annotations

from django.db import transaction
from django.utils import timezone

from .core import (AlreadyCast, Eligibility, NotEligible, NotOpen, OpenToAll,
                   Result, Revisability, Tally, UnknownChoice, Visibility)
from .models import Ballot, Question, Status                      # noqa: F401

from django.db.models import Count, Sum


def electorate_for(question) -> object:
    """Who may answer this question. Always everyone signed in.

    Kept as a function rather than deleted because `toto.forum` calls it, and
    because the day a scope wants a narrower audience this is where that
    belongs — a *visibility* rule, not an instrument of authority. There is no
    registry behind it any more: a consultation whose audience could be
    configured per scope is one step from being a formal vote again.
    """
    return OpenToAll()


def standing(question, user, *, electorate=None) -> Eligibility:
    """This user's standing, without casting anything. Never raises."""
    roll = electorate or electorate_for(question)
    try:
        return roll.standing(question, user)
    except Exception:  # noqa: BLE001 — an electorate that errors is not a crash
        return Eligibility(False, reason="Your standing could not be checked.")


@transaction.atomic
def cast(question, user, choice, *, electorate=None) -> Ballot:
    """Record one answer. Raises a VotingError subclass with a reason, or returns.

    Order matters and is the order somebody would argue about afterwards:
    is it open, are you allowed, is that a real option, have you already voted.
    """
    if not question.is_open:
        raise NotOpen("This is closed. No more answers can be recorded.")

    if choice.question_id != question.pk:
        raise UnknownChoice("That option does not belong to this question.")

    verdict = standing(question, user, electorate=electorate)
    if not verdict.allowed:
        raise NotEligible(verdict.reason or "You may not vote on this.")

    # Locked, so two clicks on a slow connection cannot become two ballots. The
    # unique constraint would catch it either way; this turns a database error
    # into a sentence.
    existing = (Ballot.objects.select_for_update()
                .filter(question=question, voter=user).first())

    if existing is None:
        return Ballot.objects.create(question=question, choice=choice,
                                     voter=user, weight=verdict.weight)

    if question.revisability == Revisability.FINAL:
        raise AlreadyCast(
            "You have already voted, and a ballot cannot be changed.")

    if existing.choice_id == choice.pk:
        return existing

    existing.choice = choice
    # The weight is NOT refreshed. It was fixed when the first ballot was cast,
    # and a revisable poll changing somebody's weight halfway through would make
    # the tally depend on when they last clicked.
    existing.revised_at = timezone.now()
    existing.revisions += 1
    existing.save(update_fields=["choice", "revised_at", "revisions"])
    return existing


def ballot_of(question, user):
    """This user's ballot, or None. Cheap enough to call on every render."""
    if not getattr(user, "is_authenticated", False):
        return None
    return Ballot.objects.filter(question=question, voter=user).first()


def may_see_results(question, user, *, electorate=None) -> bool:
    """Whether this person may read the tally yet.

    A running tally on a formal vote is an instrument for changing its outcome,
    which is why ON_CLOSE exists and why it is the default for anything called
    a vote.
    """
    if question.visibility == Visibility.LIVE:
        return True
    if question.is_open:
        return False
    if question.visibility == Visibility.ON_CLOSE:
        return True
    # ON_CLOSE_PRIVATE — closed, and only to the electorate.
    return standing(question, user, electorate=electorate).allowed


def tally(question, *, electorate=None) -> Tally:
    """Count it. Two aggregate queries regardless of how many options there are.

    Every option appears in the result, including ones nobody chose: a bar
    chart missing its empty bars misreports the shape of an opinion, and "no
    votes for this" is information.
    """
    counted = {
        row["choice_id"]: row
        for row in (question.ballots
                    .values("choice_id")
                    .annotate(weight=Sum("weight"), ballots=Count("id")))
    }

    results = []
    for choice in question.choices.all():
        row = counted.get(choice.pk)
        results.append(Result(
            choice_id=choice.pk, label=choice.label, text=choice.text,
            weight=int(row["weight"]) if row else 0,
            ballots=int(row["ballots"]) if row else 0,
        ))

    roll = electorate or electorate_for(question)
    try:
        size = int(roll.size(question) or 0)
    except Exception:  # noqa: BLE001 — a turnout is not worth an error page
        size = 0

    return Tally(
        results=tuple(results),
        total_weight=sum(r.weight for r in results),
        total_ballots=sum(r.ballots for r in results),
        electorate=size,
    )
