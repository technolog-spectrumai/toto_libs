"""Encrypted snapshots of poll actions and their results."""

from __future__ import annotations

import json

from . import keys, sealing
from .models import ForumPollAudit


def record(poll, action, actor=None):
    """Keep the question, options and counts even if the poll is removed."""
    from . import voting

    key = keys.open_key(poll.channel)
    count = voting.tally(poll, key)
    snapshot = {
        "title": voting.open_title(key, poll),
        "description": voting.open_description(key, poll),
        "status": poll.effective_status,
        "deadline": poll.closes_at.isoformat() if poll.closes_at else None,
        "total": count.total_ballots,
        "options": [{"label": item.label, "text": item.text, "votes": item.ballots}
                    for item in count.results],
    }
    row = ForumPollAudit(channel=poll.channel, poll_id=poll.pk, action=action, actor=actor)
    row.results_sealed = sealing.seal_bytes(
        key, json.dumps(snapshot, ensure_ascii=False).encode("utf-8"),
        kind="poll-audit", channel_id=poll.channel_id, message_id=row.pk)
    row.save()
    return row


def read(row):
    key = keys.open_key(row.channel)
    return json.loads(sealing.open_bytes(
        key, row.results_sealed, kind="poll-audit", channel_id=row.channel_id,
        message_id=row.pk).decode("utf-8"))
