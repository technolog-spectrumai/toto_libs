"""Message persistence and history replay.

Messages are stored in plaintext. Confidentiality in transit is TLS; the row itself is
readable by the server, which is precisely what makes permanent, paginated, searchable
history possible — including for a member who joins long after a conversation happened.

This module replaced ``vault.py``, which encrypted every message under a per-channel
gervazy DEK held in a system strongbox. That scheme capped history at a 24h TTL, required
a deployment secret whose loss made history unrecoverable, and made the message body
impossible to query. See ``forum_todo.md`` for the retention work that replaces the TTL.
"""
import logging

log = logging.getLogger(__name__)

# How many messages a freshly-connected socket receives. Older ones are fetched on demand
# by the paginated history endpoint.
DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 200


def store_message(channel, *, msg_type, body="", sender=None, sender_name="",
                  sender_avatar_url="", reply_to=None, attachment=None,
                  attachment_name="", attachment_mime="", attachment_size=None):
    """Persist a message and return the saved row.

    ``attachment`` is an uploaded file object (or ``None``); it is written to
    ``MEDIA_ROOT`` by the model's ``upload_to`` callable, never inlined into the row.
    """
    from .models import TelegraphMessage

    message = TelegraphMessage(
        channel=channel,
        sender=sender,
        sender_name=sender_name or "",
        sender_avatar_url=sender_avatar_url or "",
        msg_type=msg_type,
        body=body or "",
        reply_to=reply_to,
        attachment_name=attachment_name or "",
        attachment_mime=attachment_mime or "",
        attachment_size=attachment_size,
    )
    if attachment is not None:
        # save=False: the row has no pk row yet, and upload_to needs message.id, which the
        # model default has already generated.
        message.attachment.save(attachment_name or "upload", attachment, save=False)
    message.save()
    return message


def message_to_dict(row, *, history=False, absolute=None):
    """Render one row into the wire payload the client expects.

    The shape is identical for live broadcasts and replayed history so the browser's
    ``renderedIds`` dedup works across both. ``absolute`` is an optional callable that
    turns a relative media/avatar URL into an absolute one.
    """
    def _url(value):
        if not value:
            return ""
        return absolute(value) if absolute else value

    payload = {
        "type": row.msg_type,
        "id": str(row.id),
        "user": row.sender_name,
        "avatar_url": _url(row.sender_avatar_url),
        "created_at": row.created_at.isoformat(),
        "message": "" if row.deleted_at else row.body,
    }
    if row.edited_at:
        payload["edited_at"] = row.edited_at.isoformat()
    if row.deleted_at:
        payload["deleted"] = True
    if row.reply_to_id:
        payload["reply_to"] = str(row.reply_to_id)
    if history:
        payload["history"] = True

    if row.attachment and not row.deleted_at:
        url = _url(row.attachment.url)
        if row.msg_type == "image_message":
            payload["image_url"] = url
        elif row.msg_type == "voice_message":
            payload["audio_url"] = url
        payload["attachment_name"] = row.attachment_name

    return payload


def history(channel, *, limit=DEFAULT_HISTORY_LIMIT, before=None, absolute=None):
    """Return up to ``limit`` messages oldest-first, ending just before ``before``.

    ``before`` is a ``created_at`` datetime used as the pagination cursor: pass the
    oldest message you already hold to fetch the page above it.
    """
    limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
    qs = channel.messages.select_related("reply_to").order_by("-created_at")
    if before is not None:
        qs = qs.filter(created_at__lt=before)
    rows = list(qs[:limit])
    rows.reverse()  # oldest-first for natural append
    return [message_to_dict(row, history=True, absolute=absolute) for row in rows]


def has_more_before(channel, oldest):
    """Whether any message predates ``oldest`` (drives the client's load-older affordance)."""
    if oldest is None:
        return False
    return channel.messages.filter(created_at__lt=oldest).exists()
