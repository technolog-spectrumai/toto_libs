"""The room's half of the polls contract: scope and UI, never the rules.

Polls owns the data and the voting behaviour; this module owns exactly two
things — the scoped listing door, and the shape of a poll a room opens. The
engine's cast/tally/visibility are called through ``toto.polls.services``
with the registry-resolved room electorate; nothing here counts anything.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils.translation import gettext as _

MAX_OPTIONS = 10


def questions_for(channel):
    """Every poll belonging to this room, and nothing else.

    The single door — Business Center's argument verbatim: room A's polls and
    room B's are different sets, and the way to keep them that way is to make
    "all polls" impossible to ask for by accident.
    """
    from toto.polls.models import SCOPE_FORUM, Kind, Question

    return (Question.objects.in_scope(SCOPE_FORUM, str(channel.pk))
            .filter(kind=Kind.POLL))


def parse_options(raw: str) -> list:
    """One option per line: ``Label`` or ``Label: descriptive text`` —
    the polls form's own parsing rule, restated for the modal."""
    parsed = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        label, _sep, text = line.partition(":")
        parsed.append((label.strip()[:60], text.strip()[:300]))
    labels = [label for label, _text in parsed]
    if len(parsed) < 2:
        raise ValidationError(_("A poll needs at least two options."))
    if len(parsed) > MAX_OPTIONS:
        raise ValidationError(
            _("At most %(max)s options.") % {"max": MAX_OPTIONS})
    if len(set(labels)) != len(labels):
        raise ValidationError(_("Option labels must be distinct."))
    return parsed


@transaction.atomic
def open_room_poll(channel, user, *, title, options, closes_at=None):
    """A member's poll: lightweight, revisable, counted live.

    The engine supplies the invariants (slug, scoping); the room supplies the
    scope pair and nothing else. No electorate key is written — the scope
    default registered in electorates.py resolves the room roll.
    """
    from toto.polls.models import SCOPE_FORUM, Choice, Kind, Question

    parsed = parse_options(options)
    question = Question.objects.create(
        kind=Kind.POLL,
        title=title,
        question_text=title,
        scope_type=SCOPE_FORUM,
        scope_id=str(channel.pk),
        closes_at=closes_at,
        created_by=user,
    )
    Choice.objects.bulk_create([
        Choice(question=question, label=label, text=text, position=position)
        for position, (label, text) in enumerate(parsed)
    ])
    return question
