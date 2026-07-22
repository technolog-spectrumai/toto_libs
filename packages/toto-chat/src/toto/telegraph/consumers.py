import json
from urllib.parse import urlparse

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .store import DEFAULT_HISTORY_LIMIT


class ChatConsumer(AsyncWebsocketConsumer):
    """Forum channel socket: live delivery plus a replay of recent history on connect.

    Everything on this socket is plaintext over TLS. There is no MLS relay, no
    client-side encryption and no CRDT mirror — the database is the single source of
    truth for the message list.
    """

    async def connect(self):
        self.channel_slug = self.scope["url_route"]["kwargs"]["channel_slug"]
        self.channel_group_name = f"telegraph_{self.channel_slug}"

        user = self.scope.get("user")

        if not await self.user_can_send(user):
            await self.close(code=4403)
            return

        await self.channel_layer.group_add(
            self.channel_group_name,
            self.channel_name,
        )
        await self.accept()
        await self.send_history()
        await self.broadcast_participants()

    async def disconnect(self, close_code):
        await self.broadcast_typing(stop=True)
        await self.channel_layer.group_discard(
            self.channel_group_name,
            self.channel_name,
        )
        await self.broadcast_participants()

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

        stored = await self.persist_message(
            user,
            body=data.get("message", ""),
            sender_name=member.display_name,
            sender_avatar_url=self.absolute_url(member.avatar_url),
            reply_to_id=data.get("reply_to"),
        )
        if not stored:
            await self.send_error("Could not store the message.")
            return

        await self.broadcast(payload=stored, sender_channel=self.channel_name)

    async def handle_message_edit(self, user, data):
        message_id = (data.get("id") or "").strip()
        body = data.get("message", "")
        if not message_id or not body.strip():
            await self.send_error("message_edit requires id and a non-empty message.")
            return

        payload = await self._edit_message(user, message_id, body)
        if not payload:
            await self.send_error("You can only edit your own messages.")
            return
        await self.broadcast(payload=payload, sender_channel=None)

    async def handle_message_delete(self, user, data):
        message_id = (data.get("id") or "").strip()
        if not message_id:
            await self.send_error("message_delete requires id.")
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
        from .models import TelegraphChannel, TelegraphMessage

        try:
            channel = TelegraphChannel.objects.get(slug=self.channel_slug)
        except TelegraphChannel.DoesNotExist:
            return None

        reply_to = None
        if reply_to_id:
            reply_to = TelegraphMessage.objects.filter(
                id=reply_to_id, channel=channel
            ).first()

        row = store.store_message(
            channel,
            msg_type="chat_message",
            body=body,
            sender=user if getattr(user, "is_authenticated", False) else None,
            sender_name=sender_name or "",
            sender_avatar_url=sender_avatar_url or "",
            reply_to=reply_to,
        )
        return store.message_to_dict(row, absolute=self.absolute_url)

    async def persist_message(self, user, *, body, sender_name, sender_avatar_url,
                              reply_to_id=None):
        return await self._store_message(
            body, sender_name, sender_avatar_url, user, reply_to_id
        )

    @database_sync_to_async
    def _edit_message(self, user, message_id, body):
        from django.utils import timezone

        from . import store
        from .models import TelegraphMessage

        row = TelegraphMessage.objects.filter(
            id=message_id, channel__slug=self.channel_slug, deleted_at__isnull=True
        ).first()
        if not row or not row.sender_id or row.sender_id != user.id:
            return None
        row.body = body
        row.edited_at = timezone.now()
        row.save(update_fields=["body", "edited_at"])
        payload = store.message_to_dict(row, absolute=self.absolute_url)
        payload["type"] = "message_edit"
        payload["msg_type"] = row.msg_type
        return payload

    @database_sync_to_async
    def _delete_message(self, user, message_id):
        from django.utils import timezone

        from .models import TelegraphMessage

        row = TelegraphMessage.objects.filter(
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
        from .models import TelegraphChannel

        try:
            channel = TelegraphChannel.objects.get(slug=self.channel_slug)
        except TelegraphChannel.DoesNotExist:
            return [], False

        messages = store.history(
            channel, limit=DEFAULT_HISTORY_LIMIT, absolute=self.absolute_url
        )
        oldest = messages[0]["created_at"] if messages else None
        has_more = store.has_more_before(channel, oldest) if oldest else False
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
        await self.send(text_data=json.dumps(event["payload"]))

    async def send_error(self, message):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "system_error",
                    "user": "System",
                    "message": message,
                }
            )
        )

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
        from toto.telegraph.models import TelegraphChannel

        channel = TelegraphChannel.objects.get(slug=self.channel_slug)
        members_qs = channel.telegraph_members.filter(is_active=True).select_related(
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
                "type": member.participant_type,
            }
            for member in members_qs
        ]

        return {
            "type": "room_participants",
            "room_slug": self.channel_slug,
            "participant_count": len(members),
            "participants": members,
        }

    @database_sync_to_async
    def get_channel_member(self, user):
        from toto.telegraph.models import TelegraphChannel

        channel = TelegraphChannel.objects.get(slug=self.channel_slug)
        return channel.telegraph_members.select_related("person__user").get(
            person__user=user,
            is_active=True,
        )

    @database_sync_to_async
    def user_can_send(self, user):
        from .permissions import can_send

        return can_send(user, self.channel_slug)
