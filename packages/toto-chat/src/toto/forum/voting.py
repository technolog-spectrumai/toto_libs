"""Channel polls: opening one, answering it, counting it.

The rules are the old forum's, unchanged, because each was argued about once
already:

* **the order of the checks in :func:`cast`** — is it open, is that a real
  option, are you allowed, have you already answered. That is the order
  somebody disputes afterwards, so it is the order the code asks in.
* **one member, one answer**, with no weights.
* **an answer is final** (the owner, 2026-10-07: "all answers are final and
  cannot be changed"): every poll is opened so, :func:`cast` refuses a second
  answer whatever the poll's row says, and ``PollBallot.save`` never alters
  one. A poll's count is shown as it grows or only once it has closed, as
  its opener chose.
* **at most three open polls a channel** (``MAX_OPEN_POLLS``).

What is new is where the words are kept: a poll's question and every
option's label and text are sealed under the channel's key (``sealing``,
kinds ``poll`` and ``choice``), so nothing of what a poll asks is readable
in the database. The ballots are relations (who chose which option) and are
not sealed. Every change (opened, answered, closed, removed) takes the
channel's next event number, so the feed tells an open page of it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from . import access, channels, sealing

#: The most options one poll may carry. A radio list longer than this is a
#: survey, and a survey is a different product.
MAX_OPTIONS = 10

#: A channel holds at most this many polls that still take answers (the
#: owner, 2026-10-07: "Each forum can have at max 3 open polls"). A closed
#: poll, one past its closing time and a removed one do not count.
MAX_OPEN_POLLS = 3


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

    def share(self, result) -> float:
        """Percent of the answers cast — not of the community. A bar that
        shrank when somebody joined would be reporting attendance."""
        if not self.total_ballots:
            return 0.0
        return round(result.ballots * 100.0 / self.total_ballots, 1)


def parse_options(raw):
    """Turn the options into ``[(label, text)]``, or raise ValidationError.

    One option per line (or per list item), either ``Label`` or
    ``Label: longer text``. Blank lines are skipped rather than refused,
    because a trailing newline is not a mistake worth an error message.
    """
    if isinstance(raw, (list, tuple)):
        lines = [str(item) if isinstance(item, (str, int, float)) else "" for item in raw]
    else:
        lines = str(raw or "").splitlines()
    options = []
    seen = set()
    for line in lines:
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


def _storable(text: str) -> bool:
    if "\x00" in text:
        return False
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _seal_choice(key, poll, position, label, text) -> bytes:
    data = json.dumps({"label": label, "text": text}, ensure_ascii=False).encode("utf-8")
    return sealing.seal_bytes(key, data, kind="choice", channel_id=poll.channel_id,
                              message_id=f"{poll.id}:{position}")


def open_choice(key, choice) -> tuple[str, str]:
    """``(label, text)`` of a stored option; a marker if it cannot be read."""
    try:
        data = json.loads(sealing.open_bytes(
            key, choice.sealed, kind="choice", channel_id=choice.poll.channel_id,
            message_id=f"{choice.poll_id}:{choice.position}").decode("utf-8"))
        return str(data.get("label", "")), str(data.get("text", ""))
    except (sealing.SealBroken, ValueError):
        return "[unreadable]", ""


def open_title(key, poll) -> str:
    if poll.title_sealed is None:
        return ""
    try:
        return sealing.open_bytes(key, poll.title_sealed, kind="poll",
                                  channel_id=poll.channel_id, message_id=poll.id).decode("utf-8")
    except (sealing.SealBroken, ValueError):
        return "[unreadable]"


@transaction.atomic
def open_poll(channel, user, key, *, title, options, closes_at=None,
              revisability=None, visibility=None):
    """Create a poll in this channel with its options, sealed."""
    from .models import ChannelPoll, PollChoice, PollStatus, ResultVisibility, Revisability
    from .posting import display_name

    title = str(title or "").strip()[:150]
    if not title:
        raise ValidationError(_("A poll needs a question."))
    parsed = parse_options(options)
    if not _storable(title) or not all(_storable(a) and _storable(b) for a, b in parsed):
        raise ValidationError(_("The text holds a character that cannot be stored."))
    if revisability not in (None, "") and revisability not in Revisability.values:
        raise ValidationError(_("Choose whether answers may be changed."))
    if visibility not in (None, "") and visibility not in ResultVisibility.values:
        raise ValidationError(_("Choose when the count is shown."))
    if closes_at is not None and closes_at <= timezone.now():
        raise ValidationError(_("The closing time must be in the future."))

    # The channel's row is locked from here (next_seq), so two openings at
    # once are counted one after the other.
    number = channels.next_seq(channel)
    now = timezone.now()
    still_open = (channel.polls.filter(removed_at__isnull=True, status=PollStatus.OPEN)
                  .filter(models.Q(closes_at__isnull=True) | models.Q(closes_at__gt=now)).count())
    if still_open >= MAX_OPEN_POLLS:
        raise ValidationError(
            _("This channel already has %(n)d open polls. Close one before opening another.")
            % {"n": MAX_OPEN_POLLS})
    # Every answer is final (the owner, 2026-10-07: "all answers are final
    # and cannot be changed"): whatever was asked for, the poll is made so.
    poll = ChannelPoll(
        channel=channel, number=number, seq=number, closes_at=closes_at, created_by=user,
        opener_name=display_name(user),
        revisability=Revisability.FINAL,
        visibility=visibility or ResultVisibility.LIVE)
    poll.title_sealed = sealing.seal_bytes(key, title.encode("utf-8"), kind="poll",
                                           channel_id=channel.pk, message_id=poll.id)
    poll.save()
    PollChoice.objects.bulk_create([
        PollChoice(poll=poll, position=index,
                   sealed=_seal_choice(key, poll, index, label, text))
        for index, (label, text) in enumerate(parsed)
    ])
    return poll


def may_answer(poll, user):
    """Whether this user may answer this poll, and why not if they may not:
    who may read the poll's channel, nothing else."""
    if not getattr(user, "is_authenticated", False):
        return False, _("Sign in to answer.")
    if not access.may_read(user, poll.channel.community):
        return False, _("Only members of this community answer here.")
    return True, ""


def _touch(poll) -> None:
    poll.seq = channels.next_seq(poll.channel)
    poll.save(update_fields=["seq"])


@transaction.atomic
def cast(poll, user, choice):
    """Record one answer, or raise a VotingError whose message is the reason."""
    from .models import PollBallot

    if not poll.is_open:
        raise NotOpen(_("This poll is closed. No more answers can be recorded."))

    if choice is None or choice.poll_id != poll.pk:
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
        ballot = PollBallot.objects.create(poll=poll, choice=choice, voter=user)
        _touch(poll)
        return ballot

    # Final for every poll, one opened before 2026-10-07 with changeable
    # answers too: the row's ``revisability`` is not asked.
    raise AlreadyAnswered(
        _("You have already answered, and this poll takes one answer."))


@transaction.atomic
def close_poll(poll) -> bool:
    """Shut it by hand. False if it was closed already."""
    from .models import PollStatus

    if poll.status != PollStatus.OPEN or poll.removed_at is not None:
        return False
    poll.close()
    _touch(poll)
    return True


@transaction.atomic
def remove_poll(poll, user) -> bool:
    """Wipe a poll — its question, its options and its ballots — and leave a
    tombstone. False if it was removed already. The ballots go by a bulk
    delete: a removed poll has no final answers left to protect."""
    if poll.removed_at is not None:
        return False
    poll.ballots.all().delete()
    poll.choices.all().delete()
    poll.title_sealed = None
    poll.removed_at = timezone.now()
    poll.removed_by = user
    poll.seq = channels.next_seq(poll.channel)
    poll.save(update_fields=["title_sealed", "removed_at", "removed_by", "seq"])
    return True


def tally(poll, key) -> Tally:
    """Count it. One aggregate query regardless of how many options there
    are. Every option appears, including ones nobody chose: "no answers for
    this" is information."""
    counted = {
        row["choice_id"]: row["ballots"]
        # The trailing order_by() defeats the Meta-ordering GROUP BY trap:
        # PollBallot orders by cast_at, and Django folds an ORDER BY column
        # into the GROUP BY — one row per ballot instead of one per option.
        for row in (poll.ballots.values("choice_id")
                    .annotate(ballots=models.Count("id")).order_by("choice_id"))
    }
    results = []
    for choice in poll.choices.all():
        label, text = open_choice(key, choice)
        results.append(Result(choice_id=choice.pk, label=label, text=text,
                              ballots=int(counted.get(choice.pk, 0))))
    return Tally(results=tuple(results), total_ballots=sum(r.ballots for r in results))


def ballot_of(poll, user):
    """This user's answer, or None."""
    from .models import PollBallot

    if not getattr(user, "is_authenticated", False):
        return None
    return PollBallot.objects.filter(poll=poll, voter=user).first()


def polls_to_dicts(polls, *, key, user, moderator=False) -> list:
    """The polls as the page gets them, removed ones as tombstones. Three
    queries for the lot, not three per poll."""
    from .models import PollBallot, PollChoice

    live = [poll for poll in polls if poll.removed_at is None]
    choices: dict = {}
    counts: dict = {}
    mine: dict = {}
    if live:
        for choice in PollChoice.objects.filter(poll__in=live).select_related("poll"):
            choices.setdefault(choice.poll_id, []).append(choice)
        for row in (PollBallot.objects.filter(poll__in=live).values("poll_id", "choice_id")
                    .annotate(n=models.Count("id")).order_by("poll_id", "choice_id")):
            counts.setdefault(row["poll_id"], {})[row["choice_id"]] = row["n"]
        mine = dict(PollBallot.objects.filter(poll__in=live, voter=user)
                    .values_list("poll_id", "choice_id"))

    out = []
    for poll in polls:
        if poll.removed_at is not None:
            out.append({"id": str(poll.id), "number": poll.number, "seq": poll.seq,
                        "removed": True})
            continue
        visible = poll.results_visible
        per_choice = counts.get(poll.pk, {})
        rows = []
        for choice in choices.get(poll.pk, []):
            label, text = open_choice(key, choice)
            rows.append({"id": choice.pk, "label": label, "text": text,
                         "ballots": int(per_choice.get(choice.pk, 0)) if visible else None})
        owner = poll.created_by_id is not None and poll.created_by_id == user.pk
        out.append({
            "id": str(poll.id), "number": poll.number, "seq": poll.seq,
            "title": open_title(key, poll), "status": poll.status, "open": poll.is_open,
            "closes_at": poll.closes_at.isoformat() if poll.closes_at else None,
            "revisability": poll.revisability, "visibility": poll.visibility,
            "created_at": poll.created_at.isoformat(), "opener": poll.opener_name,
            "mine": owner, "may_manage": bool(owner or moderator),
            "choices": rows,
            "total": sum(per_choice.values()) if visible else None,
            "my_choice": mine.get(poll.pk), "results_visible": visible,
        })
    return out
