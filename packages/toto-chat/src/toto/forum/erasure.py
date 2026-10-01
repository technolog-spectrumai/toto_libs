"""An erased member in the forum (2026-10-01, 37c.21; called by toto.core's
``erase_user`` through ``toto.core.erasure``).

A message copies its sender's display name and avatar URL onto the row, so
history renders without the account — and so, once an account was erased
(the sender link is SET_NULL), its name and picture stayed on every message
it ever sent. Now:

* **the text stays**, under a neutral label and with no picture: it is the
  room's record, and replies are anchored to it;
* **what they sent as a picture or a voice recording goes**, bytes and all —
  a voice is personal data whatever it says, and a picture usually is. A
  post that was nothing but the attachment goes with it, row and all; one
  with a caption keeps the caption as an ordinary message.

Plain and encrypted rooms alike: the label is a plain column in both, and a
sealed attachment is one blob on the same disk. A sealed caption is read
with the room key to tell a bare post from a captioned one; a room whose
key cannot be opened keeps the row, without its attachment.

The bytes go after the erase commits (:func:`delete_blobs`), row first and
blob second, as ``cleanup`` does it for its stated reason.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.utils import translation
from django.utils.translation import gettext, gettext_noop

log = logging.getLogger(__name__)

#: What an erased member's messages are signed with, in the platform's own
#: language — a stored label, like the display name it replaces.
FORMER_MEMBER = gettext_noop("Former member")


def former_member_label() -> str:
    with translation.override(settings.LANGUAGE_CODE):
        return gettext(FORMER_MEMBER)


def _sent(user):
    from .models import ForumMessage

    return ForumMessage.objects.filter(sender=user)


def _with_attachment(rows):
    return rows.exclude(attachment="").exclude(attachment__isnull=True)


def sent_by(user) -> dict:
    """What :func:`forget_sender` would touch — counts for the erase report."""
    rows = _sent(user)
    return {"messages": rows.count(), "attachments": _with_attachment(rows).count()}


def _has_text(row) -> bool:
    if not row.is_sealed:
        return bool(row.body.strip())
    from . import sealing
    from .rooms import RoomKeyUnavailable, open_key

    try:
        key = open_key(row.channel)
        return bool(sealing.open_text(key, row.body_sealed, channel_id=row.channel_id,
                                      message_id=row.id).strip())
    except (RoomKeyUnavailable, sealing.SealBroken):
        return True                    # unreadable here: the row is kept, unread


def forget_sender(user) -> list[str]:
    """Sign what ``user`` sent with the neutral label and take its pictures
    and recordings (module docstring). Returns the attachments' stored names,
    for :func:`delete_blobs` once the erase has committed."""
    rows = _sent(user)
    names, bare = [], []
    for row in _with_attachment(rows).select_related("channel"):
        names.append(row.attachment.name)
        if not _has_text(row):
            bare.append(row.pk)
    from .models import ForumMessage

    ForumMessage.objects.filter(pk__in=bare).delete()
    _with_attachment(rows).update(attachment="", attachment_name="", attachment_mime="",
                                  attachment_size=None, attachment_sealed=False,
                                  msg_type="chat_message")
    rows.update(sender_name=former_member_label(), sender_avatar_url="")
    return names


def delete_blobs(names) -> None:
    """Unlink the attachments' bytes, through the field's own storage. A
    failure is logged: the rows are already gone or emptied."""
    from .models import ForumMessage

    storage = ForumMessage._meta.get_field("attachment").storage
    for name in names:
        try:
            if name and storage.exists(name):
                storage.delete(name)
        except Exception:  # noqa: BLE001 - one stuck file never undoes the erase
            log.warning("forum: could not delete an erased member's attachment %r", name,
                        exc_info=True)
