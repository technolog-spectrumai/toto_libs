"""Room polls: opening one, answering it, counting it.

This used to be half a contract. The rules lived in ``toto.polls`` — a separate
app with its own pages, its own dashboard tile and its own price — and this
module owned only the scope and the shape of the form. The engine has moved in:
polls are a forum feature now, the data hangs off :class:`RoomPoll` by a real
ForeignKey, and there is no registry between a room and its own question.

Two things came across from the old engine unchanged, because both were argued
about once already and settling them again would be a step backwards:

* **the order of the checks in :func:`cast`** — is it open, is that a real
  option, are you allowed, have you already answered. That is the order
  somebody disputes afterwards, so it is the order the code asks in.
* **a revision never re-reads the answerer's standing.** A poll that changed
  who counts halfway through would make the tally depend on when people last
  clicked.

What did NOT come across: weights. The old room electorate answered 1 for every
member, always, so a weight column here would be a column of ones and a whole
vocabulary ("weighted", "roll", "electorate") for a distinction rooms do not
make. One member, one answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

#: The most options one poll may carry. A radio list longer than this is a
#: survey, and a survey is a different product.
MAX_OPTIONS = 10


class VotingError(Exception):
    """Something refused an answer, and the message says what to tell them."""


class NotOpen(VotingError):
    pass


class NotEligible(VotingError):
    pass


class AlreadyAnswered(VotingError):
    pass


class UnknownChoice(VotingError):
    pass


@dataclass(frozen=True)
class Result:
    choice_id: int
    label: str
    text: str
    ballots: int


@dataclass(frozen=True)
class Tally:
    results: tuple
    total_ballots: int
    audience: int

    def share(self, result) -> float:
        """Percent of the answers cast — not of the room. A bar that shrank
        when somebody joined would be reporting attendance, not opinion."""
        if not self.total_ballots:
            return 0.0
        return round(result.ballots * 100.0 / self.total_ballots, 1)


def polls_for(channel):
    """Every poll belonging to this room, and nothing else.

    The single door. Room A's polls and room B's are different sets, and the
    way to keep them that way is to make "all polls" awkward to ask for.
    """
    return channel.polls.select_related("channel").prefetch_related("choices")


def page_of(channel, user):
    """Every poll in the room with its count and this user's answer.

    Three queries for the page instead of three PER POLL: the tally was a
    per-card aggregate plus a per-card audience count plus a per-card ballot
    lookup, which is fine for the two polls a test makes and is not fine for a
    room that has been running for a year.
    """
    from .models import PollBallot

    polls = list(polls_for(channel))
    if not polls:
        return []

    counts: dict[int, dict[int, int]] = {}
    rows = (PollBallot.objects.filter(poll__in=polls)
            .values("poll_id", "choice_id")
            .annotate(n=models.Count("id"))
            .order_by("poll_id", "choice_id"))
    for row in rows:
        counts.setdefault(row["poll_id"], {})[row["choice_id"]] = row["n"]

    mine = {}
    if getattr(user, "is_authenticated", False):
        mine = {b.poll_id: b for b in
                PollBallot.objects.filter(poll__in=polls, voter=user)
                .select_related("choice")}

    audience = channel.forum_members.filter(is_active=True).count()
    out = []
    for poll in polls:
        per_choice = counts.get(poll.pk, {})
        results = tuple(
            Result(choice_id=c.pk, label=c.label, text=c.text,
                   ballots=int(per_choice.get(c.pk, 0)))
            for c in poll.choices.all())
        out.append((poll,
                    Tally(results=results,
                          total_ballots=sum(r.ballots for r in results),
                          audience=audience),
                    mine.get(poll.pk)))
    return out


def may_answer(poll, user):
    """Whether this user may answer this poll, and why not if they may not.

    Membership of the poll's own room, nothing else — the rule the old
    RoomAudience registered with the engine, now asked directly.
    """
    from . import permissions

    if not getattr(user, "is_authenticated", False):
        return False, _("Sign in to answer.")
    if permissions.member_for(user, poll.channel) is None:
        return False, _("Only members of this room answer here.")
    return True, ""


def audience_size(poll) -> int:
    """How many people could answer — active members of the room."""
    return poll.channel.forum_members.filter(is_active=True).count()


def parse_options(raw):
    """Turn the textarea into labels, or raise ValidationError.

    One option per line, either ``Label`` or ``Label: longer text``. Blank
    lines are skipped rather than refused, because a trailing newline is not a
    mistake worth an error message.
    """
    options = []
    seen = set()
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        label, _sep, text = line.partition(":")
        label = label.strip()[:60]
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            raise ValidationError(_("Two options cannot share a label."))
        seen.add(key)
        options.append((label, text.strip()[:300]))

    if len(options) < 2:
        raise ValidationError(_("A poll needs at least two options."))
    if len(options) > MAX_OPTIONS:
        raise ValidationError(
            _("A poll takes at most %(n)d options.") % {"n": MAX_OPTIONS})
    return options


@transaction.atomic
def open_poll(channel, user, *, title, options, closes_at=None,
              revisability=None, visibility=None):
    """Create a poll in this room with its options."""
    from .models import PollChoice, ResultVisibility, Revisability, RoomPoll

    title = (title or "").strip()[:150]
    if not title:
        raise ValidationError(_("A poll needs a question."))
    parsed = parse_options(options)

    poll = RoomPoll(
        channel=channel, title=title, question_text=title,
        closes_at=closes_at, created_by=user,
        revisability=revisability or Revisability.OPEN,
        visibility=visibility or ResultVisibility.LIVE,
    )
    poll.slug = _unique_slug(channel, title)
    poll.save()
    PollChoice.objects.bulk_create([
        PollChoice(poll=poll, label=label, text=text, position=index)
        for index, (label, text) in enumerate(parsed)
    ])
    return poll


def _unique_slug(channel, title) -> str:
    """A slug free within THIS room. Scoped, so two rooms may both hold a
    poll called "lunch" — the uniqueness constraint is per channel."""
    from django.utils.text import slugify

    from .models import RoomPoll

    base = slugify(title)[:160] or "poll"
    slug, suffix = base, 1
    while RoomPoll.objects.filter(channel=channel, slug=slug).exists():
        suffix += 1
        slug = f"{base}-{suffix}"[:170]
    return slug


@transaction.atomic
def cast(poll, user, choice):
    """Record one answer, or raise a VotingError whose message is the reason."""
    from .models import PollBallot, Revisability

    if not poll.is_open:
        raise NotOpen(_("This poll is closed. No more answers can be recorded."))

    if choice.poll_id != poll.pk:
        raise UnknownChoice(_("That option does not belong to this poll."))

    allowed, reason = may_answer(poll, user)
    if not allowed:
        raise NotEligible(reason)

    # Locked, so two clicks on a slow connection cannot become two ballots.
    # The unique constraint would catch it either way; this turns a database
    # error into a sentence.
    existing = (PollBallot.objects.select_for_update()
                .filter(poll=poll, voter=user).first())

    if existing is None:
        return PollBallot.objects.create(poll=poll, choice=choice, voter=user)

    if poll.revisability == Revisability.FINAL:
        raise AlreadyAnswered(
            _("You have already answered, and this poll takes one answer."))

    if existing.choice_id == choice.pk:
        return existing

    existing.choice = choice
    existing.revised_at = timezone.now()
    existing.revisions += 1
    existing.save(update_fields=["choice", "revised_at", "revisions"])
    return existing


def ballot_of(poll, user):
    """This user's answer, or None. Cheap enough to call on every render."""
    from .models import PollBallot

    if not getattr(user, "is_authenticated", False):
        return None
    return PollBallot.objects.filter(poll=poll, voter=user).first()


def tally(poll) -> Tally:
    """Count it. One aggregate query regardless of how many options there are.

    Every option appears in the result, including ones nobody chose: a bar
    chart missing its empty bars misreports the shape of an opinion, and "no
    answers for this" is information.
    """
    counted = {
        row["choice_id"]: row["ballots"]
        # The trailing order_by() defeats the Meta-ordering GROUP BY trap:
        # PollBallot orders by cast_at, and Django folds an ORDER BY column
        # into the GROUP BY — one row per ballot instead of one per option.
        for row in (poll.ballots.values("choice_id")
                    .annotate(ballots=models.Count("id"))
                    .order_by("choice_id"))
    }
    results = tuple(
        Result(choice_id=choice.pk, label=choice.label, text=choice.text,
               ballots=int(counted.get(choice.pk, 0)))
        for choice in poll.choices.all()
    )
    return Tally(results=results,
                 total_ballots=sum(r.ballots for r in results),
                 audience=audience_size(poll))


def may_manage(poll, user) -> bool:
    """Who may close or delete a poll: whoever opened it, or staff.

    `is_superuser` does not imply `is_staff` in Django, so both count — the
    predicate every operator gate in this suite uses.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if poll.created_by_id and poll.created_by_id == user.id:
        return True
    return bool(getattr(user, "is_staff", False)
                or getattr(user, "is_superuser", False))
