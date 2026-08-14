"""Casting a ballot and counting them. The only door into either.

Every consumer — the Polls pages, the Forum room, Business Center — goes
through :func:`cast`. That is the point: the rules that make a formal vote
trustworthy (closed means closed, one ballot per voter, cast once, weight
frozen) are worth nothing if three call sites each implement them, because the
third one will get it wrong and nobody will notice until a result is disputed.

The old app's voting was ``Vote.objects.update_or_create(...)`` inline in a
view, which silently made every ballot editable forever. That is fine for a
poll and disqualifying for a vote, and it is exactly the kind of decision that
has to be stated rather than inherited from whichever line of code got written
first.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .core import (AlreadyCast, Eligibility, NotEligible, NotOpen,
                   Result, Revisability, Tally, UnknownChoice, Visibility)
from .models import Ballot, Decision, Kind, Outcome, Question, Status


def electorate_for(question) -> object:
    """The electorate a question names, resolved through the registry.

    Resolved here rather than stored on the row, because an electorate is code
    (it asks live questions about membership and shareholdings) and the row
    stores only a key. Consumers may still pass ``electorate=`` explicitly at
    any call site; that override always wins.
    """
    from . import electorates

    return electorates.resolve(question)


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


# -- recording a decision -----------------------------------------------------

def record_decision(question, *, decided_by=None, when=None) -> Decision:
    """Close a formal vote and write its result down, once.

    The Tally is recomputed on every page view; the Decision is the one copy
    that is not — "what was decided" has to survive later edits and deletions.
    Idempotent: recording an already-decided vote returns the existing row.

    Policy: after the deadline the clock decides and anyone (or nothing —
    ``decided_by=None``) may trigger the recording. BEFORE the deadline, or
    when there is no deadline at all, closing is an act of authority: only
    staff may do it, and the row says who.
    """
    if question.kind != Kind.VOTE:
        raise ValueError("Only a formal vote is recorded. A poll just closes.")

    with transaction.atomic():
        question = (Question.objects.select_for_update()
                    .get(pk=question.pk))
        existing = Decision.objects.filter(question=question).first()
        if existing is not None:
            return existing

        now = timezone.now()
        deadline_passed = (question.closes_at is not None
                           and question.closes_at <= now)
        if not deadline_passed:
            if decided_by is None or not (decided_by.is_staff
                                          or decided_by.is_superuser):
                raise PermissionDenied(
                    "Closing a vote before its deadline is a staff act.")

        from . import electorates

        roll = electorate_for(question)
        count = tally(question, electorate=roll)

        if count.total_ballots == 0:
            outcome, winner_label, winner_ref = Outcome.NO_BALLOTS, "", None
        elif count.winner is None:
            outcome, winner_label, winner_ref = Outcome.TIE, "", None
        else:
            outcome = Outcome.WINNER
            winner_label = count.winner.label
            winner_ref = {"choice_id": count.winner.choice_id,
                          "label": count.winner.label}

        # An overdue vote closed AT its deadline, whenever the recording ran;
        # an early close closed when the closer said so.
        closed_when = when or (question.closes_at if deadline_passed else now)
        question.close(when=closed_when)

        proposer = question.created_by
        ballots = [{
            "voter_id": b.voter_id,
            "voter_username": b.voter.get_username(),
            "choice_label": b.choice.label,
            "weight": b.weight,
            "cast_at": b.cast_at.isoformat(),
        } for b in question.ballots.select_related("voter", "choice")]

        content = {
            "question": {
                "pk": question.pk, "kind": question.kind,
                "title": question.title,
                "question_text": question.question_text,
                "body": question.body, "slug": question.slug,
                "scope_type": question.scope_type,
                "scope_id": question.scope_id,
            },
            "proposer": ({"id": proposer.pk,
                          "username": proposer.get_username()}
                         if proposer else None),
            "window": {
                "opens_at": question.opens_at.isoformat(),
                "closes_at": (question.closes_at.isoformat()
                              if question.closes_at else None),
                "closed_at": (question.closed_at.isoformat()
                              if question.closed_at else None),
            },
            "electorate": {"key": electorates.key_of(question),
                           "size": count.electorate},
            "outcome": {"outcome": outcome, "winner": winner_ref},
            "tally": [{
                "choice_id": r.choice_id, "label": r.label,
                "weight": r.weight, "ballots": r.ballots,
            } for r in count.results],
            "totals": {"weight": count.total_weight,
                       "ballots": count.total_ballots,
                       "turnout": count.turnout},
            "ballots": ballots,
        }

        try:
            return Decision.objects.create(
                question=question,
                scope_type=question.scope_type, scope_id=question.scope_id,
                title=question.title,
                outcome=outcome, winner_label=winner_label,
                electorate_key=electorates.key_of(question),
                electorate_size=count.electorate,
                total_ballots=count.total_ballots,
                total_weight=count.total_weight,
                turnout=count.turnout,
                decided_by=decided_by,
                content=content,
            )
        except IntegrityError:
            # Two recorders raced past the filter; the OneToOne held. The
            # other one's row IS the decision.
            return Decision.objects.get(question=question)


def record_overdue(scope_type: str = "", scope_id: str = "") -> int:
    """Record every formal vote in one scope whose deadline has passed.

    Called from the pages that could display a decided vote — the tab, the
    ledger, the results page — so the decision exists by the time anything
    could show it. Deterministic: after the deadline no ballot can change
    (cast() checks the clock), so it does not matter which request runs it.
    Bounded: one indexed query, usually empty.
    """
    overdue = (Question.objects.in_scope(scope_type, scope_id)
               .filter(kind=Kind.VOTE, status=Status.OPEN,
                       closes_at__lte=timezone.now(),
                       decision__isnull=True))
    recorded = 0
    for question in overdue:
        record_decision(question)
        recorded += 1
    return recorded
