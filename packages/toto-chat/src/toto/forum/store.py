"""Message persistence and history replay.

Messages are stored in plaintext. Confidentiality in transit is TLS; the row itself is
readable by the server, which is precisely what makes permanent, paginated, searchable
history possible — including for a member who joins long after a conversation happened.

This module replaced ``vault.py``, which encrypted every message under a per-channel
gervazy DEK held in a system strongbox. That scheme capped history at a 24h TTL, required
a deployment secret whose loss made history unrecoverable, and made the message body
impossible to query. The retention work that replaces the TTL is deferred.
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
    from .models import ForumMessage

    message = ForumMessage(
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
        # Never the raw storage URL: attachments live outside MEDIA_ROOT and are served
        # only by the membership-checked view. See models.forum_attachment_storage.
        from django.urls import reverse

        url = _url(reverse("forum:api_message_attachment", args=[row.id]))
        if row.msg_type == "image_message":
            payload["image_url"] = url
        elif row.msg_type == "voice_message":
            payload["audio_url"] = url
        payload["attachment_name"] = row.attachment_name

    return payload


def _before_filter(before, before_id):
    """Keyset predicate for "strictly older than the (created_at, id) cursor".

    Ordering on ``created_at`` alone is not a total order — two messages can share a
    timestamp — so a plain ``created_at__lt`` cursor silently drops one of them when the
    page boundary falls between them, and the sort itself is unstable. Pairing the
    timestamp with the primary key makes both the order and the cursor total.
    """
    from django.db.models import Q

    if before_id is None:
        return Q(created_at__lt=before)
    return Q(created_at__lt=before) | Q(created_at=before, id__lt=before_id)


def history(channel, *, limit=DEFAULT_HISTORY_LIMIT, before=None, before_id=None,
            absolute=None):
    """Return up to ``limit`` messages oldest-first, ending just before the cursor.

    The cursor is the ``(created_at, id)`` pair of the oldest message you already hold;
    pass both to page backwards without dropping or repeating rows.
    """
    limit = max(1, min(int(limit or DEFAULT_HISTORY_LIMIT), MAX_HISTORY_LIMIT))
    qs = channel.messages.select_related("reply_to").order_by("-created_at", "-id")
    if before is not None:
        qs = qs.filter(_before_filter(before, before_id))
    rows = list(qs[:limit])
    rows.reverse()  # oldest-first for natural append
    return [message_to_dict(row, history=True, absolute=absolute) for row in rows]


def has_more_before(channel, oldest, oldest_id=None):
    """Whether any message precedes the cursor (drives the load-older affordance)."""
    if oldest is None:
        return False
    return channel.messages.filter(_before_filter(oldest, oldest_id)).exists()
