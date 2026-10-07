"""An erased member in the forum (called by toto.core's ``erase_user``
through ``toto.core.erasure``).

A message copies its sender's display name onto the row, so history reads
without the account — and so, once an account was erased (the sender link is
SET_NULL), its name stayed on every message it ever sent. So:

* **the text stays**, under a neutral label: it is the channel's record;
* **the pictures they sent go**, the vault's rows and bytes: a picture is
  usually personal data. A post that was nothing but a picture goes with it,
  row and all (it leaves a tombstone, so open pages drop it); one with text
  keeps the text as an ordinary message;
* **the polls they opened stay**, signed with the neutral label; their
  ballots go with the account (the voter link cascades).

A channel whose key cannot be opened keeps the row, without its picture.
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


def sent_by(user) -> dict:
    """What :func:`forget_sender` would touch — counts for the erase report."""
    rows = _sent(user)
    return {"messages": rows.filter(removed_at__isnull=True).count(),
            "attachments": rows.filter(attachment__isnull=False).count()}


def forget_sender(user) -> list:
    """Sign what ``user`` sent with the neutral label and take their
    pictures. Returns ``[]``: the pictures' bytes leave through the vault
    (``images.drop``), after the erase commits, so nothing is left for
    :func:`delete_blobs`."""
    from . import channels, images
    from .models import ChannelPoll

    for row in _sent(user).filter(attachment__isnull=False).select_related("channel"):
        bare = not row.text_bytes
        images.drop(row)
        row.kind = row.TEXT
        row.attachment_mime, row.attachment_size = "", None
        fields = ["kind", "attachment_mime", "attachment_size"]
        if bare and row.removed_at is None:
            # Nothing but the picture: a tombstone, so open pages drop it.
            from django.utils import timezone

            row.seq = channels.next_seq(row.channel)
            row.removed_at = timezone.now()
            row.body_sealed = None
            fields += ["seq", "removed_at", "body_sealed"]
        row.save(update_fields=fields)
    label = former_member_label()
    _sent(user).update(sender_name=label)
    ChannelPoll.objects.filter(created_by=user).update(opener_name=label)
    return []


def delete_blobs(names) -> None:
    """Kept for toto.core's erase, which calls it with what
    :func:`forget_sender` returned: nothing, since the vault removes the
    bytes itself."""
    return None
