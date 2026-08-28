"""What was posted in this room, as files.

The Files tab used to show something else entirely: `library.py`'s vault
directory, filled by uploading through the vault's own gateway page. That
library and the chat were two disjoint file systems — a photo somebody posted
in the room had never appeared on the room's Files tab, and a file on the Files
tab had never been part of any conversation.

This module is the other answer, and the one the tab shows now: **a file enters
a room by being posted in it, and nowhere else.** There is no upload door here,
deliberately. The chat already has one — with its size cap, its MIME allow-list
and its membership check — and a second door onto the same room would be a
second set of rules to keep in step.

The vault library is *not* deleted; its files stay where they are, reachable
through Storage. What changed is only which of the two the room shows.
"""

from __future__ import annotations

from django.db import models

#: Rows the tab groups by. `msg_type` says which of these a message is; the
#: last is the catch-all for anything a future upload door might add, so a new
#: kind of attachment appears in the list rather than vanishing from it.
KIND_IMAGE = "image"
KIND_VOICE = "voice"
KIND_FILE = "file"

_KIND_BY_MSG_TYPE = {
    "image_message": KIND_IMAGE,
    "voice_message": KIND_VOICE,
}


def kind_of(message) -> str:
    return _KIND_BY_MSG_TYPE.get(message.msg_type, KIND_FILE)


def room_attachments(channel, *, search="", kind=""):
    """Every file posted in this room, newest first.

    Rooted at ``channel.messages`` and never at ``ForumMessage.objects`` — the
    scope is the point, and a queryset that started from the manager would be
    one filter away from showing another room's files.

    ``deleted_at__isnull=True`` is the deletion rule in one line: a file
    follows its message. Removing a message removes the file it carried from
    this tab too, because the two are one act — somebody withdrawing what they
    posted. The bytes stay on disk until retention removes them with the row.
    """
    rows = (channel.messages
            .filter(deleted_at__isnull=True)
            .exclude(attachment="")
            .exclude(attachment__isnull=True)
            .select_related("sender")
            # (created_at, id): created_at is not a total order, which is the
            # same reason the history cursor is a pair rather than a timestamp.
            .order_by("-created_at", "-id"))

    if kind == KIND_IMAGE:
        rows = rows.filter(msg_type="image_message")
    elif kind == KIND_VOICE:
        rows = rows.filter(msg_type="voice_message")
    elif kind == KIND_FILE:
        rows = rows.exclude(msg_type__in=("image_message", "voice_message"))

    if search:
        # The filename, and the words posted with it: a voice note is called
        # "voice-message.webm" every single time, so searching names alone
        # would make every recording unfindable.
        rows = rows.filter(models.Q(attachment_name__icontains=search)
                           | models.Q(body__icontains=search))
    return rows


def summarise(rows) -> dict:
    """Count and total size in one query, for the line above the list."""
    totals = rows.aggregate(count=models.Count("id"),
                            total=models.Sum("attachment_size"))
    return {"count": totals["count"] or 0, "bytes": totals["total"] or 0}
