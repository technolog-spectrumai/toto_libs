"""Poll threads, one ballot per member, explicit vote reset and audit."""

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

#: Each member may create this many threads in each community per UTC day.
MAX_THREADS_PER_DAY = 3
MAX_TITLE_CHARS = 256
MAX_DESCRIPTION_CHARS = 2048


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


def open_description(key, poll) -> str:
    if poll.description_sealed is None:
        return ""
    try:
        return sealing.open_bytes(key, poll.description_sealed, kind="poll-description",
                                  channel_id=poll.channel_id, message_id=poll.id).decode("utf-8")
    except (sealing.SealBroken, ValueError):
        return "[unreadable]"


@transaction.atomic
def open_poll(channel, user, key, *, title, description="", options, closes_at=None,
              visibility=None):
    """Create a poll in this channel with its options, sealed."""
    from .models import ChannelPoll, ForumPollAudit, PollChoice, ResultVisibility
    from . import poll_audit
    from .posting import display_name

    title = str(title or "").strip()
    description = str(description or "").strip()
    if not title:
        raise ValidationError(_("A poll needs a question."))
    if len(title) > MAX_TITLE_CHARS:
        raise ValidationError(_("The title may have at most %(n)d characters.")
                              % {"n": MAX_TITLE_CHARS})
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValidationError(_("The description may have at most %(n)d characters.")
                              % {"n": MAX_DESCRIPTION_CHARS})
    parsed = parse_options(options)
    if not _storable(title) or not _storable(description) or not all(
            _storable(a) and _storable(b) for a, b in parsed):
        raise ValidationError(_("The text holds a character that cannot be stored."))
    if visibility not in (None, "") and visibility not in ResultVisibility.values:
        raise ValidationError(_("Choose when the count is shown."))
    if closes_at is None:
        raise ValidationError(_("Choose a deadline for voting."))
    if closes_at <= timezone.now():
        raise ValidationError(_("The closing time must be in the future."))

    # The channel's row is locked from here (next_seq), so two openings at
    # once are counted one after the other.
    number = channels.next_seq(channel)
    now = timezone.now()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    made_today = ForumPollAudit.objects.filter(
        channel=channel, actor=user, action="create",
        created_at__gte=start_of_day).count()
    if made_today >= MAX_THREADS_PER_DAY:
        raise ValidationError(
            _("You may create at most %(n)d threads in this community per day.")
            % {"n": MAX_THREADS_PER_DAY})
    poll = ChannelPoll(
        channel=channel, number=number, seq=number, closes_at=closes_at, created_by=user,
        opener_name=display_name(user), last_activity_at=now,
        visibility=visibility or ResultVisibility.LIVE)
    poll.title_sealed = sealing.seal_bytes(key, title.encode("utf-8"), kind="poll",
                                           channel_id=channel.pk, message_id=poll.id)
    poll.description_sealed = sealing.seal_bytes(
        key, description.encode("utf-8"), kind="poll-description",
        channel_id=channel.pk, message_id=poll.id)
    poll.save()
    PollChoice.objects.bulk_create([
        PollChoice(poll=poll, position=index,
                   sealed=_seal_choice(key, poll, index, label, text))
        for index, (label, text) in enumerate(parsed)
    ])
    from . import poll_storage
    poll_storage.ensure(poll, user)
    poll_audit.record(poll, "create", user)
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
    from .models import ChannelPoll, ForumChannel, PollBallot

    ForumChannel.objects.select_for_update().get(pk=poll.channel_id)
    locked = ChannelPoll.objects.select_for_update().get(pk=poll.pk)
    poll.status, poll.closes_at, poll.removed_at = (
        locked.status, locked.closes_at, locked.removed_at)

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

    raise AlreadyAnswered(
        _("Reset your vote before choosing again."))


@transaction.atomic
def reset_vote(poll, user) -> bool:
    """Only an explicit reset removes one's ballot; a fresh cast follows."""
    from .models import ChannelPoll, ForumChannel, PollBallot

    ForumChannel.objects.select_for_update().get(pk=poll.channel_id)
    locked = ChannelPoll.objects.select_for_update().get(pk=poll.pk)
    poll.status, poll.closes_at, poll.removed_at = (
        locked.status, locked.closes_at, locked.removed_at)

    if not poll.is_open:
        raise NotOpen(_("This poll is closed. No more answers can be recorded."))
    ballot = PollBallot.objects.select_for_update().filter(poll=poll, voter=user).first()
    if ballot is None:
        return False
    ballot.delete()
    _touch(poll)
    return True


@transaction.atomic
def close_poll(poll, actor=None) -> bool:
    """Shut it by hand. False if it was closed already."""
    from .models import ChannelPoll, ForumChannel, PollStatus
    from . import poll_audit

    ForumChannel.objects.select_for_update().get(pk=poll.channel_id)
    locked = ChannelPoll.objects.select_for_update().get(pk=poll.pk)
    poll.status, poll.removed_at = locked.status, locked.removed_at
    if poll.status != PollStatus.OPEN or poll.removed_at is not None:
        return False
    poll.close()
    _touch(poll)
    poll_audit.record(poll, "close", actor)
    return True


def close_due(poll) -> bool:
    """Persist a passed deadline when the poll is next touched."""
    from .models import PollStatus

    if poll.status == PollStatus.OPEN and poll.closes_at and poll.closes_at <= timezone.now():
        return close_poll(poll)
    return False


@transaction.atomic
def archive_poll(poll, actor) -> bool:
    from .models import ChannelPoll, ForumChannel, PollStatus
    from . import poll_audit

    ForumChannel.objects.select_for_update().get(pk=poll.channel_id)
    locked = ChannelPoll.objects.select_for_update().get(pk=poll.pk)
    poll.status, poll.closes_at, poll.removed_at = (
        locked.status, locked.closes_at, locked.removed_at)
    close_due(poll)
    if poll.removed_at is not None or poll.status != PollStatus.CLOSED:
        raise ValidationError(_("Only a closed thread can be archived."))
    poll.status = PollStatus.ARCHIVED
    poll.seq = channels.next_seq(poll.channel)
    poll.save(update_fields=["status", "seq"])
    poll_audit.record(poll, "archive", actor)
    return True


@transaction.atomic
def remove_poll(poll, user) -> bool:
    """Wipe a poll — its question, its options and its ballots — and leave a
    tombstone. False if it was removed already. The ballots go by a bulk
    delete: a removed poll has no final answers left to protect."""
    from .models import ChannelPoll, ForumChannel

    ForumChannel.objects.select_for_update().get(pk=poll.channel_id)
    locked = ChannelPoll.objects.select_for_update().get(pk=poll.pk)
    poll.removed_at = locked.removed_at
    if poll.removed_at is not None:
        return False
    from . import poll_audit
    poll_audit.record(poll, "delete", user)
    from .cleanup import _remove_messages

    _remove_messages(list(poll.messages.select_related("attachment")))
    from . import poll_storage
    poll_storage.empty(poll)
    poll.ballots.all().delete()
    poll.choices.all().delete()
    poll.title_sealed = None
    poll.description_sealed = None
    poll.removed_at = timezone.now()
    poll.removed_by = user
    poll.seq = channels.next_seq(poll.channel)
    poll.save(update_fields=["title_sealed", "description_sealed", "removed_at", "removed_by", "seq"])
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
    from .models import ForumMessage, PollBallot, PollChoice

    for poll in polls:
        close_due(poll)
    live = [poll for poll in polls if poll.removed_at is None]
    choices: dict = {}
    counts: dict = {}
    mine: dict = {}
    message_counts: dict = {}
    if live:
        for choice in PollChoice.objects.filter(poll__in=live).select_related("poll"):
            choices.setdefault(choice.poll_id, []).append(choice)
        for row in (PollBallot.objects.filter(poll__in=live).values("poll_id", "choice_id")
                    .annotate(n=models.Count("id")).order_by("poll_id", "choice_id")):
            counts.setdefault(row["poll_id"], {})[row["choice_id"]] = row["n"]
        mine = dict(PollBallot.objects.filter(poll__in=live, voter=user)
                    .values_list("poll_id", "choice_id"))
        message_counts = dict(ForumMessage.objects.filter(
            poll__in=live, removed_at__isnull=True).values("poll_id")
            .annotate(n=models.Count("id")).values_list("poll_id", "n"))

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
            "title": open_title(key, poll), "description": open_description(key, poll),
            "status": poll.effective_status, "open": poll.is_open,
            "closes_at": poll.closes_at.isoformat() if poll.closes_at else None,
            "visibility": poll.visibility,
            "created_at": poll.created_at.isoformat(),
            "last_activity_at": poll.last_activity_at.isoformat(), "opener": poll.opener_name,
            "mine": owner, "may_manage": bool(owner or moderator),
            "choices": rows,
            "total": sum(per_choice.values()) if visible else None,
            "my_choice": mine.get(poll.pk), "results_visible": visible,
            "comment_count": message_counts.get(poll.pk, 0),
        })
    return out
