"""Messages: posting one, removing one, and the feed a page reads.

**Posting** (``post_message``). Text, an image, or both. Every press carries
an ``op``: a UUID the page mints once per deliberate press and repeats on a
retry. It is bound to the request by a keyed digest over the member, the op,
the text and the image's hash, and kept on the row:

    the same op, the same request     the message already stored (a replay);
                                      nothing is stored or charged again
    the same op, another request      409: the op bought one post
    a fresh op                        the caps and the funds are checked
                                      first (``billing.afford_post``: 429 or
                                      402, nothing stored); then the
                                      message, its image, and the charge
                                      (``billing.settle_post``) in ONE
                                      transaction

The database has the last word on a race: (sender, op) is unique, so of two
requests sent at once under one op one stores and the other is answered as
a replay or refused.

**Removing** (``remove_message``) wipes the content at once and for good —
the sealed text, the image's row and bytes in the vault — and leaves the row
as a tombstone with a new ``seq``, so a page that holds the message learns
it is gone.

**The feed** (``feed``). ``after=<seq>`` answers every message and poll
changed since that event, tombstones included, and the new cursor; without a
cursor it answers the newest messages and the polls; ``before=<number>``
pages older history. Nothing is held open: it is one short query.
"""

from __future__ import annotations

import json
import uuid

from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.translation import gettext as _

from . import access, billing, channels, images, keys, sealing

#: The most UTF-8 bytes of one message's text.
MAX_TEXT_BYTES = 8 * 1024

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
#: The most polls one feed answer carries.
MAX_POLLS = 100

#: Posts per member per minute, across channels.
POSTS_PER_MINUTE = 30

#: Search (the channel's Search tab). A channel's content is sealed at rest,
#: so the database cannot be asked for a word: the newest ``SEARCH_SCAN``
#: messages are opened one by one. At most ``SEARCH_HITS`` are answered.
SEARCH_HITS = 50
SEARCH_SCAN = 2000
SEARCH_MIN, SEARCH_MAX = 2, 100
SEARCHES_PER_MINUTE = 20

#: The Images tab: this many pictures a page at the most.
IMAGES_PAGE = 60

_SALT = "toto.forum.posting"


class Refusal(Exception):
    """A door's own refusal: ``status_code`` and a sentence for the member."""

    def __init__(self, message, status_code=400, retry_after=None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def clean_op(value) -> str:
    """The op as a canonical UUID string, or 400."""
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise Refusal(_("This request carries no operation id. Reload the page and try again."),
                      400) from None


def clean_text(value, *, most=MAX_TEXT_BYTES) -> str:
    """Text that can be kept: a string, no NUL (PostgreSQL refuses one),
    writable as UTF-8, within ``most`` bytes. Else 400."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise Refusal(_("The text must be text."), 400)
    text = value.strip()
    if "\x00" in text:
        raise Refusal(_("The text holds a character that cannot be stored."), 400)
    try:
        size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        raise Refusal(_("The text holds a character that cannot be stored."), 400) from None
    if size > most:
        raise Refusal(_("The text is too long: %(size)d bytes, and the most is %(most)d.")
                      % {"size": size, "most": most}, 413)
    return text


def digest(user, op, text, image_hash, poll_id=None) -> str:
    """The keyed digest that binds an op to one request of one member."""
    material = json.dumps([user.pk, op, text, image_hash, str(poll_id)], separators=(",", ":"),
                          ensure_ascii=True)
    return salted_hmac(_SALT, material, algorithm="sha256").hexdigest()


def _used() -> Refusal:
    return Refusal(_("This request was already used. Press the button again to send a new one."),
                   409)


def _known(user, op, request_digest):
    """The message ``op`` already stored for ``user``, if it was this very
    request; None for a fresh op; 409 for an op used for another request."""
    from .models import ForumMessage

    row = ForumMessage.objects.filter(sender=user, op_key=op).first()
    if row is None:
        return None
    if row.op_digest == request_digest:
        return row
    raise _used()


def _key(channel) -> bytes:
    try:
        return keys.open_key(channel)
    except keys.ChannelKeyUnavailable:
        raise Refusal(_("The forum's key is not available on this server, so nothing can be "
                        "read or stored. Tell an administrator."), 503) from None


def display_name(user) -> str:
    from toto.people.models import Person

    person = Person.objects.filter(user=user).first()
    name = (person.full_name if person is not None else "") or user.get_username()
    return str(name)[:150]


def post_message(user, channel, *, poll, text, op, image=None):
    """Store one message. Returns ``(message, replay)``.

    ``image`` is ``(bytes, type)`` as ``images.read_upload`` answers, or
    None. The caller has asked ``access.may_post``.
    """
    from toto.core import ratelimit

    from .models import ForumMessage

    op = clean_op(op)
    text = clean_text(text)
    if (poll is None or poll.channel_id != channel.pk or poll.removed_at is not None
            or poll.status == "archived"):
        raise Refusal(_("This thread is not available in this community."), 404)
    data, mime = image if image is not None else (None, "")
    if not text and data is None:
        raise Refusal(_("Write something, or choose an image."), 400)
    request_digest = digest(user, op, text, images.digest(data) if data is not None else "",
                            poll.pk)

    known = _known(user, op, request_digest)
    if known is not None:
        return known, True

    key = _key(channel)
    try:
        ratelimit.check(f"forum:post:{user.pk}", limit=POSTS_PER_MINUTE, window=60)
    except ratelimit.RateLimited as exc:
        raise Refusal(_("You are posting too fast. Wait a moment."), 429,
                      getattr(exc, "retry_after", None)) from None

    text_bytes = len(text.encode("utf-8"))
    image_bytes = len(data) if data is not None else 0
    # Before anything is stored: could this member be charged for it? A
    # refusal here (the day's cap, the pool) leaves no row and no image.
    billing.afford_post(user, text_bytes=text_bytes, image_bytes=image_bytes)
    stored = None
    try:
        with transaction.atomic():
            number = channels.next_seq(channel)
            from .models import ChannelPoll

            locked_poll = ChannelPoll.objects.select_for_update().filter(
                pk=poll.pk, channel=channel, removed_at__isnull=True).first()
            if locked_poll is None or locked_poll.status == "archived":
                raise Refusal(_("This thread is not available in this community."), 404)
            message = ForumMessage(
                channel=channel, poll=locked_poll, number=number, seq=number, sender=user,
                sender_name=display_name(user),
                kind=ForumMessage.IMAGE if data is not None else ForumMessage.TEXT,
                text_bytes=text_bytes, op_key=op, op_digest=request_digest)
            message.body_sealed = sealing.seal_text(key, text, channel_id=channel.pk,
                                                    message_id=message.id)
            message.save()
            if data is not None:
                stored = images.store(locked_poll, user, message.id, data, mime, key)
                message.attachment = stored
                message.attachment_mime = mime
                message.attachment_size = len(data)
                message.save(update_fields=["attachment", "attachment_mime", "attachment_size"])
            # THE CHARGE, in the message's own transaction (billing.py): a
            # post that fails after it is rolled back with it, and a charge
            # that is refused takes the message with it.
            billing.settle_post(user, message, text_bytes=text_bytes,
                                image_bytes=image_bytes)
            locked_poll.seq = number
            locked_poll.last_activity_at = message.created_at
            locked_poll.save(update_fields=["seq", "last_activity_at"])
    except IntegrityError:
        # Another request stored this op first; this one's rows are undone.
        if stored is not None:
            images.discard(stored)
        known = _known(user, op, request_digest)
        if known is None:
            raise
        return known, True
    except billing.InsufficientFunds:
        # The pool ran short between the check and the charge (another
        # request spent it). The message and its row in the vault are undone;
        # ask the check again, which now refuses in the pool's own sentence.
        if stored is not None:
            images.discard(stored)
        billing.afford_post(user, text_bytes=text_bytes, image_bytes=image_bytes)
        raise
    except Exception:
        if stored is not None:
            images.discard(stored)
        raise
    return message, False


def remove_message(user, message) -> bool:
    """Wipe a message's content and leave its tombstone. False if it was
    removed already. The caller has asked ``access.may_remove_message``."""
    from .models import ForumMessage

    with transaction.atomic():
        row = ForumMessage.objects.select_for_update().get(pk=message.pk)
        if row.removed_at is not None:
            return False
        row.seq = channels.next_seq(row.channel)
        row.removed_at = timezone.now()
        row.removed_by = user
        row.body_sealed = None
        row.text_bytes = 0
        row.save(update_fields=["seq", "removed_at", "removed_by", "body_sealed", "text_bytes"])
        images.drop(row)
        row.attachment_mime = ""
        row.attachment_size = None
        row.save(update_fields=["attachment_mime", "attachment_size"])
    message.refresh_from_db()
    return True


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _avatars(rows) -> dict:
    """``{user id: avatar url}`` for the senders of ``rows``, in one query."""
    from toto.people.models import Person

    ids = {row.sender_id for row in rows if row.sender_id}
    out = {}
    if not ids:
        return out
    for person in Person.objects.filter(user_id__in=ids).exclude(avatar="").only("user", "avatar"):
        try:
            if person.avatar:
                out[person.user_id] = person.avatar.url
        except ValueError:
            continue
    return out


def message_to_dict(row, *, key, user, slug, moderator=False, avatars=None) -> dict:
    """One message as the page gets it; a removed one as its tombstone."""
    if row.removed_at is not None:
        return {"id": str(row.id), "number": row.number, "seq": row.seq, "removed": True}
    try:
        text = (sealing.open_text(key, row.body_sealed, channel_id=row.channel_id,
                                  message_id=row.id) if row.body_sealed is not None else "")
        unreadable = False
    except sealing.SealBroken:
        text, unreadable = "", True
    mine = row.sender_id is not None and row.sender_id == user.pk
    out = {
        "id": str(row.id), "poll_id": str(row.poll_id), "number": row.number,
        "seq": row.seq, "kind": row.kind,
        "sender": row.sender_name, "sender_id": row.sender_id, "mine": mine,
        "avatar": (avatars or {}).get(row.sender_id, ""),
        "created_at": row.created_at.isoformat(), "text": text, "image": None,
        "may_remove": bool(mine or moderator),
    }
    if unreadable:
        out["unreadable"] = True
    if row.attachment_id is not None:
        out["image"] = {"url": reverse("forum:message_image", args=[slug, row.id]),
                        "mime": row.attachment_mime, "size": row.attachment_size}
    return out


def _limit(value) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _number(value, name):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise Refusal(_("“%(name)s” must be a whole number.") % {"name": name}, 400) from None
    if number < 0 or number > 2 ** 62:
        raise Refusal(_("“%(name)s” must be a whole number.") % {"name": name}, 400)
    return number


def feed(user, channel, *, after=None, before=None, limit=None) -> dict:
    """What the page asks for (module docstring). The caller has asked
    ``access.may_read``."""
    from . import voting
    from .models import ForumChannel

    after, before, limit = _number(after, "after"), _number(before, "before"), _limit(limit)
    key = _key(channel)
    slug = channel.community.slug
    moderator = access.may_moderate(user, channel.community)
    # The cursor first, then the rows up to it: every event up to a committed
    # last_seq is committed (channels.next_seq), so nothing is skipped.
    fresh = ForumChannel.objects.only("last_seq", "purged_before").get(pk=channel.pk)
    cursor = fresh.last_seq
    purged = fresh.purged_before.isoformat() if fresh.purged_before else None

    def render(rows):
        avatars = _avatars(rows)
        return [message_to_dict(row, key=key, user=user, slug=slug, moderator=moderator,
                                avatars=avatars) for row in rows]

    if before is not None:
        rows = list(channel.messages.filter(removed_at__isnull=True, number__lt=before)
                    .order_by("-number")[:limit + 1])
        more, rows = len(rows) > limit, rows[:limit]
        rows.reverse()
        return {"messages": render(rows), "more": more,
                "oldest": rows[0].number if rows else None, "purged_before": purged}

    if after:
        rows = list(channel.messages.filter(seq__gt=after, seq__lte=cursor)
                    .order_by("seq")[:limit + 1])
        more = len(rows) > limit
        if more:
            # A long absence: answer a page and let the page ask again from
            # its end. Polls up to the same event, so nothing overtakes.
            rows = rows[:limit]
            cursor = rows[-1].seq
        polls = list(channel.polls.filter(seq__gt=after, seq__lte=cursor).order_by("seq")[:MAX_POLLS])
        return {"cursor": cursor, "messages": render(rows),
                "polls": voting.polls_to_dicts(polls, key=key, user=user,
                                               moderator=access.is_administrator(user)),
                "more": more, "purged_before": purged}

    rows = list(channel.messages.filter(removed_at__isnull=True, seq__lte=cursor)
                .order_by("-number")[:limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    rows.reverse()
    polls = list(channel.polls.filter(removed_at__isnull=True, seq__lte=cursor)
                 .order_by("-number")[:MAX_POLLS])
    polls.reverse()
    return {"cursor": cursor, "messages": render(rows),
            "polls": voting.polls_to_dicts(polls, key=key, user=user,
                                           moderator=access.is_administrator(user)),
            "more": more, "oldest": rows[0].number if rows else None,
            "purged_before": purged}


def thread_feed(user, poll, *, after=None, before=None, limit=None) -> dict:
    """Replies for one poll, newest page initially and channel-seq deltas later."""
    from . import voting
    from .models import ForumChannel

    after, before, limit = _number(after, "after"), _number(before, "before"), _limit(limit)
    channel = poll.channel
    key = _key(channel)
    fresh = ForumChannel.objects.only("last_seq", "purged_before").get(pk=channel.pk)
    cursor = fresh.last_seq
    rows = poll.messages.filter(seq__lte=cursor)
    if before is not None:
        rows = rows.filter(removed_at__isnull=True, number__lt=before)
    elif after is not None:
        rows = rows.filter(seq__gt=after)
    else:
        rows = rows.filter(removed_at__isnull=True)
    if after is not None and before is None:
        selected = list(rows.order_by("seq")[:limit + 1])
    else:
        selected = list(rows.order_by("-number")[:limit + 1])
    more = len(selected) > limit
    selected = selected[:limit]
    if after is None or before is not None:
        selected.reverse()
    avatars = _avatars(selected)
    messages = [message_to_dict(row, key=key, user=user, slug=channel.slug,
                                moderator=access.may_moderate(user, channel.community),
                                avatars=avatars) for row in selected]
    payload = {"messages": messages, "more": more,
               "purged_before": fresh.purged_before.isoformat() if fresh.purged_before else None}
    if before is not None:
        payload["oldest"] = selected[0].number if selected else None
        return payload
    if after is not None and more:
        cursor = selected[-1].seq
    payload["cursor"] = cursor
    payload["oldest"] = selected[0].number if selected and after is None else None
    payload["poll"] = voting.polls_to_dicts(
        [poll], key=key, user=user,
        moderator=access.is_administrator(user))[0]
    return payload


def search(user, channel, query) -> dict:
    """The channel's messages that hold ``query``, the newest first (the
    Search tab; the owner, 2026-10-07: "the 3rd tab is search - search
    through messages in this current forum"). The caller has asked
    ``access.may_read``.

    The words and a sender's name are compared without regard to case. The
    content is sealed, so each of the newest ``SEARCH_SCAN`` messages is
    opened to be read; ``capped`` says that there were more messages than
    that, or more hits than ``SEARCH_HITS``. Nothing is written, nothing is
    charged, and the query is kept nowhere (no log line, no usage event)."""
    from toto.core import ratelimit

    query = " ".join(str(query or "").split())
    if len(query) < SEARCH_MIN:
        raise Refusal(_("Type at least %(n)d characters to search for.") % {"n": SEARCH_MIN}, 400)
    if len(query) > SEARCH_MAX:
        raise Refusal(_("That is too long to search for: at most %(n)d characters.")
                      % {"n": SEARCH_MAX}, 400)
    try:
        ratelimit.check(f"forum:search:{user.pk}", limit=SEARCHES_PER_MINUTE, window=60)
    except ratelimit.RateLimited as exc:
        raise Refusal(_("You are searching too fast. Wait a moment."), 429,
                      getattr(exc, "retry_after", None)) from None
    needle = query.casefold()
    key = _key(channel)
    slug = channel.community.slug
    moderator = access.may_moderate(user, channel.community)
    rows = list(channel.messages.filter(removed_at__isnull=True).order_by("-number")[:SEARCH_SCAN + 1])
    capped, rows = len(rows) > SEARCH_SCAN, rows[:SEARCH_SCAN]
    avatars = _avatars(rows)
    hits = []
    for row in rows:
        shown = message_to_dict(row, key=key, user=user, slug=slug, moderator=moderator,
                                avatars=avatars)
        if needle in f"{shown.get('text') or ''}\n{shown.get('sender') or ''}".casefold():
            if len(hits) >= SEARCH_HITS:
                capped = True
                break
            hits.append(shown)
    return {"query": query, "messages": hits, "scanned": len(rows), "capped": capped}


def images_page(user, channel, *, before=None, limit=None) -> dict:
    """The channel's pictures, the newest first (the Images tab; the owner,
    2026-10-07: "4th tab is images - which just list all images added to the
    forum"): the messages that carry one, as the feed draws them, a page at a
    time (``before`` is a message's number). The caller has asked
    ``access.may_read``; a picture itself is read through the image door,
    which asks again."""
    before, limit = _number(before, "before"), min(_limit(limit), IMAGES_PAGE)
    key = _key(channel)
    slug = channel.community.slug
    moderator = access.may_moderate(user, channel.community)
    rows = channel.messages.filter(removed_at__isnull=True, attachment__isnull=False)
    if before is not None:
        rows = rows.filter(number__lt=before)
    rows = list(rows.order_by("-number")[:limit + 1])
    more, rows = len(rows) > limit, rows[:limit]
    avatars = _avatars(rows)
    return {"messages": [message_to_dict(row, key=key, user=user, slug=slug, moderator=moderator,
                                         avatars=avatars) for row in rows],
            "more": more, "oldest": rows[-1].number if rows else None}
