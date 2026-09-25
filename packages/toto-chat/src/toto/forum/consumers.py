import json
import uuid
from urllib.parse import urlparse

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from . import presence
from .store import DEFAULT_HISTORY_LIMIT


def _as_uuid(value):
    """Client-supplied message id -> UUID, or None if it is not one.

    Feeding a bare string into a filter on the UUID primary key raises
    ValidationError out of the consumer, which tears the socket down instead of
    producing an error frame.
    """
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


class ChatConsumer(AsyncWebsocketConsumer):
    """Forum channel socket: live delivery plus a replay of recent history on connect.

    The socket carries plaintext over TLS in every room. In an ENCRYPTED room the
    server seals each body under the room key before storing it and opens it again for
    delivery (SECURITY.md: encryption at rest, server-side — no client-side keys, no
    MLS relay, no CRDT mirror). The database is the single source of truth.

    A send is rate limited per sender, checked against the quota cap and the mana pool
    BEFORE it is stored, and stored and charged in one transaction — a refusal is an
    error frame and stores nothing (billing.py).
    """

    #: Three refused sends in a row close the socket (4429): a client that keeps
    #: sending past the limit is not a person typing.
    MAX_RATE_REFUSALS = 3

    async def connect(self):
        self.channel_slug = self.scope["url_route"]["kwargs"]["channel_slug"]
        self.channel_group_name = f"forum_{self.channel_slug}"

        user = self.scope.get("user")

        if not await self.user_can_send(user):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(
            self.channel_group_name,
            self.channel_name,
        )
        await self.accept()
        self.display_name = await self.member_display_name(user)
        await self.mark_present()
        await self.send_history()
        await self.broadcast_participants()

    async def disconnect(self, close_code):
        await self.broadcast_typing(stop=True)
        await self.mark_absent()
        await self.channel_layer.group_discard(
            self.channel_group_name,
            self.channel_name,
        )
        await self.broadcast_participants()

    @database_sync_to_async
    def mark_present(self):
        presence.arrive(self.channel_slug, getattr(self, "display_name", ""))

    @database_sync_to_async
    def mark_absent(self):
        presence.depart(self.channel_slug, getattr(self, "display_name", ""))

    @database_sync_to_async
    def member_display_name(self, user):
        from .permissions import member_for

        member = member_for(user, self.channel_slug)
        return member.display_name if member else ""

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self.send_error("Invalid JSON.")
            return

        user = self.scope.get("user")
        if not await self.user_can_send(user):
            await self.send_error(
                "You are observing this channel. Join as a member before sending messages."
            )
            return

        data_type = data.get("type")

        if data_type == "chat_message" and data.get("message"):
            await self.handle_chat_message(user, data)
            return

        if data_type == "message_edit":
            await self.handle_message_edit(user, data)
            return

        if data_type == "message_delete":
            await self.handle_message_delete(user, data)
            return

        if data_type in ("typing_start", "typing_stop"):
            await self.broadcast_typing(stop=data_type == "typing_stop")
            return

        await self.send_error(f"Unsupported message type: {data_type!r}")

    # ── sending ───────────────────────────────────────────────────────────────
    async def handle_chat_message(self, user, data):
        member = await self.get_channel_member(user)

        refusal = await self.check_send_allowed(user)
        if refusal is not None:
            await self.send_error(refusal["message"], code=refusal["code"],
                                  retry_after=refusal.get("retry_after"))
            if refusal["code"] == "rate_limited":
                self._rate_refusals = getattr(self, "_rate_refusals", 0) + 1
                if self._rate_refusals >= self.MAX_RATE_REFUSALS:
                    await self.close(code=4429)
            return
        self._rate_refusals = 0

        stored = await self.persist_message(
            user,
            body=data.get("message", ""),
            sender_name=member.display_name,
            sender_avatar_url=self.absolute_url(member.avatar_url),
            reply_to_id=data.get("reply_to"),
        )
        if not stored or stored.get("error"):
            await self.send_error((stored or {}).get("error") or "Could not store the message.",
                                  code=(stored or {}).get("code", "store"))
            return

        await self.broadcast(payload=stored, sender_channel=self.channel_name)

    @database_sync_to_async
    def check_send_allowed(self, user):
        """Rate limit, then quota and mana — before anything is stored."""
        from toto.core import ratelimit
        from toto.quota.api import InArrears, QuotaExceeded
        from toto.quota.charge import InsufficientFunds

        from . import billing, creation
        from .models import ForumChannel

        channel = ForumChannel.objects.filter(slug=self.channel_slug).first()
        if channel is None:
            return {"code": "gone", "message": "This room no longer exists."}
        limit, window = creation.limits()["send"]
        try:
            ratelimit.check(f"forum:send:{user.pk}", limit=limit, window=window)
        except ratelimit.RateLimited as exc:
            return {"code": "rate_limited", "message": str(exc), "retry_after": exc.retry_after}
        try:
            billing.check_affordable(user, channel)
        except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
            return {"code": "payment", "message": str(exc)}
        return None

    async def handle_message_edit(self, user, data):
        message_id = _as_uuid((data.get("id") or "").strip())
        body = data.get("message", "")
        if not message_id or not body.strip():
            await self.send_error("message_edit requires a valid id and a non-empty message.")
            return

        payload = await self._edit_message(user, message_id, body)
        if not payload:
            await self.send_error("You can only edit your own messages.")
            return
        await self.broadcast(payload=payload, sender_channel=None)

    async def handle_message_delete(self, user, data):
        message_id = _as_uuid((data.get("id") or "").strip())
        if not message_id:
            await self.send_error("message_delete requires a valid id.")
            return

        payload = await self._delete_message(user, message_id)
        if not payload:
            await self.send_error("You can only delete your own messages.")
            return
        await self.broadcast(payload=payload, sender_channel=None)

    # ── typing ────────────────────────────────────────────────────────────────
    async def broadcast_typing(self, *, stop):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            return
        try:
            member = await self.get_channel_member(user)
        except Exception:
            return
        # Typing is presence, not content — broadcast only, never persisted.
        await self.broadcast(
            payload={
                "type": "typing_stop" if stop else "typing_start",
                "user": member.display_name,
            },
            sender_channel=self.channel_name,
            echo=False,
        )

    # ── persistence ───────────────────────────────────────────────────────────
    @database_sync_to_async
    def _store_message(self, body, sender_name, sender_avatar_url, user, reply_to_id):
        from . import store
        from .models import ForumChannel, ForumMessage

        try:
            channel = ForumChannel.objects.get(slug=self.channel_slug)
        except ForumChannel.DoesNotExist:
            return None

        # Scoped to this channel, so a reply can never point at a message the sender
        # could not see. A non-UUID id is ignored rather than crashing the socket.
        reply_to = None
        reply_uuid = _as_uuid(reply_to_id) if reply_to_id else None
        if reply_uuid:
            reply_to = ForumMessage.objects.filter(
                id=reply_uuid, channel=channel
            ).first()

        from django.db import transaction

        from toto.quota.api import InArrears, QuotaExceeded
        from toto.quota.charge import InsufficientFunds

        from . import billing
        from .rooms import RoomKeyUnavailable, open_key

        try:
            key = open_key(channel) if channel.is_encrypted else None
            # Stored and charged together: the ledger post is atomic, so a
            # refused charge rolls the message back and a failed store is
            # never charged (billing.py).
            with transaction.atomic():
                row = store.store_message(
                    channel,
                    msg_type="chat_message",
                    body=body,
                    sender=user if getattr(user, "is_authenticated", False) else None,
                    sender_name=sender_name or "",
                    sender_avatar_url=sender_avatar_url or "",
                    reply_to=reply_to,
                    key=key,
                )
                billing.settle_message(user, row)
        except RoomKeyUnavailable as exc:
            return {"error": str(exc), "code": "key"}
        except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
            return {"error": str(exc), "code": "payment"}
        return store.message_to_dict(row, absolute=self.absolute_url, key=key)

    async def persist_message(self, user, *, body, sender_name, sender_avatar_url,
                              reply_to_id=None):
        return await self._store_message(
            body, sender_name, sender_avatar_url, user, reply_to_id
        )

    @database_sync_to_async
    def _edit_message(self, user, message_id, body):
        from django.utils import timezone

        from . import store
        from .models import ForumMessage

        row = ForumMessage.objects.filter(
            id=message_id, channel__slug=self.channel_slug, deleted_at__isnull=True
        ).select_related("channel").first()
        if not row or not row.sender_id or row.sender_id != user.id:
            return None
        key = None
        if row.is_sealed:
            from .rooms import RoomKeyUnavailable, open_key

            try:
                key = open_key(row.channel)
            except RoomKeyUnavailable:
                return None
            store.seal_edit(row, key, body)
        else:
            row.body = body
        row.edited_at = timezone.now()
        row.save(update_fields=["body", "body_sealed", "edited_at"])
        payload = store.message_to_dict(row, absolute=self.absolute_url, key=key)
        payload["type"] = "message_edit"
        payload["msg_type"] = row.msg_type
        return payload

    @database_sync_to_async
    def _delete_message(self, user, message_id):
        from django.utils import timezone

        from .models import ForumMessage

        row = ForumMessage.objects.filter(
            id=message_id, channel__slug=self.channel_slug, deleted_at__isnull=True
        ).first()
        if not row or not row.sender_id or row.sender_id != user.id:
            return None
        row.deleted_at = timezone.now()
        row.save(update_fields=["deleted_at"])
        return {"type": "message_delete", "id": str(row.id)}

    @database_sync_to_async
    def _load_history(self):
        from . import store
        from .models import ForumChannel

        try:
            channel = ForumChannel.objects.get(slug=self.channel_slug)
        except ForumChannel.DoesNotExist:
            return [], False

        from django.utils.dateparse import parse_datetime

        messages = store.history(
            channel, limit=DEFAULT_HISTORY_LIMIT, absolute=self.absolute_url
        )
        oldest = messages[0] if messages else None
        has_more = (
            store.has_more_before(
                channel, parse_datetime(oldest["created_at"]), oldest["id"]
            )
            if oldest else False
        )
        return messages, has_more

    async def send_history(self):
        """Replay the most recent messages to the just-connected socket."""
        try:
            messages, has_more = await self._load_history()
        except Exception:
            messages, has_more = [], False
        await self.send(text_data=json.dumps({
            "type": "chat_history",
            "room_slug": self.channel_slug,
            "messages": messages,
            "has_more": has_more,
        }))

    # ── plumbing ──────────────────────────────────────────────────────────────
    async def broadcast_participants(self):
        await self.broadcast(
            payload=await self.channel_participants_payload(),
            sender_channel=self.channel_name,
        )

    async def broadcast(self, *, payload, sender_channel, echo=True):
        await self.channel_layer.group_send(
            self.channel_group_name,
            {
                "type": "chat_message",
                "payload": payload,
                "sender_channel": sender_channel,
                "echo": echo,
            },
        )

    async def chat_message(self, event):
        # Typing indicators must not be echoed to their own sender.
        if not event.get("echo", True) and event.get("sender_channel") == self.channel_name:
            return
        # Membership was checked at connect, but a member can be removed (or leave via
        # the REST API) while the tab stays open — nothing closes the socket for them.
        # Re-check before delivering, with a short TTL so this costs at most one indexed
        # query every REVALIDATE_SECONDS per socket rather than one per message.
        if not await self.still_a_member():
            await self.close(code=4403)
            return
        await self.send(text_data=json.dumps(event["payload"]))

    # Backstop only. The authoritative signal is the membership_changed frame that
    # signals.py broadcasts on every ForumMember write; this bounds the damage if a
    # write happened while the channel layer was unavailable.
    REVALIDATE_SECONDS = 30

    async def membership_changed(self, event):
        """A ForumMember row in this channel changed — re-check without waiting."""
        self._membership_checked_at = None
        if not await self.still_a_member():
            await self.close(code=4403)

    async def still_a_member(self):
        import time

        now = time.monotonic()
        checked_at = getattr(self, "_membership_checked_at", None)
        if checked_at is not None and now - checked_at < self.REVALIDATE_SECONDS:
            return self._membership_ok
        self._membership_ok = await self.user_can_send(self.scope.get("user"))
        self._membership_checked_at = now
        return self._membership_ok

    async def send_error(self, message, *, code="", retry_after=None):
        frame = {"type": "system_error", "user": "System", "message": message}
        if code:
            frame["code"] = code
        if retry_after:
            frame["retry_after"] = retry_after
        await self.send(text_data=json.dumps(frame))

    async def room_closed(self, event):
        """The room expired and was removed: tell the tab, then close."""
        await self.send(text_data=json.dumps({"type": "room_closed"}))
        await self.close(code=4410)

    def absolute_url(self, url):
        if not url:
            return url
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return url

        headers = dict(self.scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("utf-8")
        forwarded_proto = headers.get(b"x-forwarded-proto", b"").decode("utf-8")
        scheme = forwarded_proto or ("https" if self.scope.get("scheme") == "wss" else "http")
        path = url if url.startswith("/") else f"/{url}"

        return f"{scheme}://{host}{path}" if host else path

    @database_sync_to_async
    def channel_participants_payload(self):
        from toto.forum.models import ForumChannel

        channel = ForumChannel.objects.get(slug=self.channel_slug)
        members_qs = channel.forum_members.filter(is_active=True).select_related(
            "person", "person__user"
        )
        members = [
            {
                "name": member.display_name,
                # SSO username — lets a peer resolve this member's iroh gossip
                # NodeId from aster to open a P2P room without a ticket. Empty for
                # people with no linked account.
                "username": (
                    member.person.user.username
                    if member.person and member.person.user_id
                    else ""
                ),
                "avatar_url": self.absolute_url(member.avatar_url),
            }
            for member in members_qs
        ]

        # `participants` is the roster; `online` is who actually has a socket open.
        # These used to be conflated, which made the UI show every member as online.
        connected = set(presence.online(self.channel_slug))
        for member in members:
            member["online"] = member["name"] in connected

        return {
            "type": "room_participants",
            "room_slug": self.channel_slug,
            "participant_count": len(members),
            "online_count": len(connected),
            "online": sorted(connected),
            "participants": members,
        }

    @database_sync_to_async
    def get_channel_member(self, user):
        from toto.forum.models import ForumChannel

        channel = ForumChannel.objects.get(slug=self.channel_slug)
        return channel.forum_members.select_related("person__user").get(
            person__user=user,
            is_active=True,
        )

    @database_sync_to_async
    def user_can_send(self, user):
        from .permissions import can_send

        return can_send(user, self.channel_slug)
