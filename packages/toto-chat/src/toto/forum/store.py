"""Message persistence and history replay.

Messages in an ORDINARY room are stored in plaintext. Confidentiality in transit is TLS;
the row itself is readable by the server, which is precisely what makes permanent,
paginated, searchable history possible — including for a member who joins long after a
conversation happened.

Since 2026-09-25 a room may be ENCRYPTED at rest instead, by choice when it is made:
its bodies and attachments are sealed under the room's key (rooms.py, sealing.py) and
`body` stays empty. What that costs is stated where a person chooses it: an encrypted
room is not searchable. What it does NOT bring back is what the old scheme below got
wrong — there is no TTL on a persistent room, the key is recoverable (the platform box
or, for a password room, the password), and ordinary rooms are untouched.

This module replaced ``vault.py``, which encrypted every message under a per-channel
gervazy DEK held in a system strongbox. That scheme capped history at a 24h TTL, required
a deployment secret whose loss made history unrecoverable, and made the message body
impossible to query. The retention work that replaces the TTL now lives in
`toto.forum.cleanup`: a staff-set period, swept nightly.
"""
import logging

log = logging.getLogger(__name__)

# How many messages a freshly-connected socket receives. Older ones are fetched on demand
# by the paginated history endpoint.
DEFAULT_HISTORY_LIMIT = 50
MAX_HISTORY_LIMIT = 200


def store_message(channel, *, msg_type, body="", sender=None, sender_name="",
                  sender_avatar_url="", reply_to=None, attachment=None,
                  attachment_name="", attachment_mime="", attachment_size=None,
                  key=None):
    """Persist a message and return the saved row.

    ``attachment`` is an uploaded file object (or ``None``); it is written to the
    forum's attachment storage by the model's ``upload_to`` callable, never inlined
    into the row. In an encrypted room ``key`` is the room key and both the body and
    the attachment bytes are sealed; without it an encrypted room refuses to store.
    """
    from django.core.files.base import ContentFile

    from . import sealing
    from .models import ForumMessage
    from .rooms import RoomKeyUnavailable

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
    if channel.is_encrypted:
        if key is None:
            raise RoomKeyUnavailable("An encrypted room stores nothing without its key.")
        message.body_sealed = sealing.seal_text(key, body or "", channel_id=channel.pk,
                                                message_id=message.id)
        message.body = ""
        if attachment is not None:
            sealed = sealing.seal_bytes(key, attachment.read(), kind="att",
                                        channel_id=channel.pk, message_id=message.id)
            attachment = ContentFile(sealed)
            message.attachment_sealed = True
    if attachment is not None:
        # save=False: the row has no pk row yet, and upload_to needs message.id, which the
        # model default has already generated.
        message.attachment.save(attachment_name or "upload", attachment, save=False)
    message.save()
    return message


def body_of(row, key=None) -> str:
    """The readable text of a row: plaintext, or its sealed body opened."""
    if row.deleted_at:
        return ""
    if not row.is_sealed:
        return row.body
    if key is None:
        return "[encrypted]"
    from . import sealing

    try:
        return sealing.open_text(key, row.body_sealed, channel_id=row.channel_id,
                                 message_id=row.id)
    except sealing.SealBroken:
        return "[unreadable]"


def seal_edit(row, key, text) -> None:
    """Re-seal an edited body with a fresh nonce under the same binding."""
    from . import sealing

    row.body_sealed = sealing.seal_text(key, text, channel_id=row.channel_id, message_id=row.id)
    row.body = ""


def message_to_dict(row, *, history=False, absolute=None, key=None):
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
        "message": body_of(row, key),
    }
    if row.is_sealed:
        payload["sealed"] = True
        if key is not None and payload["message"] == "[unreadable]":
            payload["unreadable"] = True
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
            absolute=None, key=None):
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
    if key is None and channel.is_encrypted:
        from .rooms import RoomKeyUnavailable, open_key

        try:
            key = open_key(channel)
        except RoomKeyUnavailable:
            key = None
    return [message_to_dict(row, history=True, absolute=absolute, key=key) for row in rows]


def has_more_before(channel, oldest, oldest_id=None):
    """Whether any message precedes the cursor (drives the load-older affordance)."""
    if oldest is None:
        return False
    return channel.messages.filter(_before_filter(oldest, oldest_id)).exists()
