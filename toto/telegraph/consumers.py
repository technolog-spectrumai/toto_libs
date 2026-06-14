import json
from urllib.parse import urlparse

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer


MLS_TYPES = {
    "mls_key_package",
    "mls_welcome",
    "mls_commit",
    "mls_app",
    "mls_reset_request",
}

YJS_TYPES = {
    "yjs_update",
    "yjs_sync_request",
    "yjs_sync_response",
}


class ChatConsumer(AsyncWebsocketConsumer):
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
        cant_send = await self.user_can_send(user)
        if not cant_send:
            await self.send_error(
                "You are observing this channel. Join as a member before sending messages."
            )
            return

        data_type = data.get("type")

        if data_type in YJS_TYPES:
            await self.handle_yjs_message(data)
            return

        if data_type in MLS_TYPES:
            await self.handle_opaque_mls_message(user, data)
            return

        if data_type == "chat_message" and data.get("message"):
            await self.handle_chat_message(user, data)
            return

        if data_type == "image_message" and data.get("image_data"):
            await self.handle_image_message(user, data)
            return

        if data_type == "voice_message" and data.get("audio_data"):
            await self.handle_voice_message(user, data)
            return

        await self.send_error(
            "Only chat messages, image messages, Yjs messages, and MLS-encrypted messages are accepted."
        )

    async def handle_yjs_message(self, data):
        data["sender_channel"] = self.channel_name

        await self.broadcast(
            payload=data,
            sender_channel=self.channel_name,
            target_channel=data.get("target_channel"),
        )

    async def handle_opaque_mls_message(self, user, data):
        """
        Fully opaque MLS relay.

        The server forwards only the cryptographic payload and routing fields.
        No user identity, avatar, or server-side metadata is injected, so the
        server cannot correlate ciphertexts to members at the application layer.
        """
        payload = {
            "type": data.get("type"),
            "sender_channel": self.channel_name,
            "target_channel": data.get("target_channel"),
        }

        for field in (
            "id",                # message dedup key used by the browser's renderedIds set
            "device_id",
            "key_package",
            "package",           # browser sends key-package bytes under this name
            "welcome",
            "commit",
            "ciphertext",
            "reset_id",          # mls_reset_request dedup key
            "sender_name",
            "sender_avatar_url",
            # handshake routing — needed for key_package/welcome exchange
            "user",
            "target",
            "device_kind",
            "mls_session_id",
            "target_device_id",
            "target_device_kind",
        ):
            if field in data:
                payload[field] = data[field]

        await self.broadcast(
            payload=payload,
            sender_channel=self.channel_name,
            target_channel=payload.get("target_channel"),
        )

    async def handle_chat_message(self, user, data):
        member = await self.get_channel_member(user)

        data["user"] = member.display_name
        data["avatar_url"] = self.absolute_url(member.avatar_url)
        data["participant_type"] = member.participant_type
        data["sender_channel"] = self.channel_name
        data["target_channel"] = None

        stored = await self.persist_message(
            user, "chat_message", {"message": data.get("message", "")},
            member.display_name, self.absolute_url(member.avatar_url),
        )
        if stored:
            data["id"] = stored["id"]
            data["created_at"] = stored["created_at"]

        await self.broadcast(
            payload=data,
            sender_channel=self.channel_name,
            target_channel=None,
        )

    async def handle_image_message(self, user, data):
        image_data = data.get("image_data", "")

        if not isinstance(image_data, str) or not image_data.startswith("data:image/"):
            await self.send_error("Invalid image data.")
            return

        # 10 MB raw ≈ 13.4 MB base64
        if len(image_data) > 14 * 1024 * 1024:
            await self.send_error("Image too large (max 10 MB).")
            return

        member = await self.get_channel_member(user)

        payload = {
            "type": "image_message",
            "image_data": image_data,
            "user": member.display_name,
            "avatar_url": self.absolute_url(member.avatar_url),
            "sender_channel": self.channel_name,
            "target_channel": None,
        }

        stored = await self.persist_message(
            user, "image_message", {"image_data": image_data},
            member.display_name, self.absolute_url(member.avatar_url),
        )
        if stored:
            payload["id"] = stored["id"]
            payload["created_at"] = stored["created_at"]

        await self.broadcast(
            payload=payload,
            sender_channel=self.channel_name,
            target_channel=None,
        )

    async def handle_voice_message(self, user, data):
        audio_data = data.get("audio_data", "")

        if not isinstance(audio_data, str) or not audio_data.startswith("data:audio/"):
            await self.send_error("Invalid audio data.")
            return

        if len(audio_data) > 14 * 1024 * 1024:
            await self.send_error("Voice message too large (max ~10 MB).")
            return

        member = await self.get_channel_member(user)

        payload = {
            "type": "voice_message",
            "audio_data": audio_data,
            "user": member.display_name,
            "avatar_url": self.absolute_url(member.avatar_url),
            "sender_channel": self.channel_name,
            "target_channel": None,
        }

        stored = await self.persist_message(
            user, "voice_message", {"audio_data": audio_data},
            member.display_name, self.absolute_url(member.avatar_url),
        )
        if stored:
            payload["id"] = stored["id"]
            payload["created_at"] = stored["created_at"]

        await self.broadcast(
            payload=payload,
            sender_channel=self.channel_name,
            target_channel=None,
        )

    # ── persistent encrypted history (Discord model) ──────────────────────────
    @database_sync_to_async
    def _store_message(self, msg_type, content, sender_name, sender_avatar_url, user):
        from . import vault
        from .models import TelegraphChannel

        try:
            channel = TelegraphChannel.objects.get(slug=self.channel_slug)
        except TelegraphChannel.DoesNotExist:
            return None
        try:
            row = vault.store_message(
                channel,
                msg_type=msg_type,
                payload=content,
                sender=user if getattr(user, "is_authenticated", False) else None,
                sender_name=sender_name or "",
                sender_avatar_url=sender_avatar_url or "",
            )
        except vault.VaultUnavailable:
            return None  # history disabled — keep the live broadcast working
        return {"id": str(row.id), "created_at": row.created_at.isoformat()}

    async def persist_message(self, user, msg_type, content, sender_name, sender_avatar_url):
        """Persist a relay message encrypted at rest; returns {id, created_at} or None."""
        try:
            return await self._store_message(
                msg_type, content, sender_name, sender_avatar_url, user
            )
        except Exception:
            return None  # never let persistence break live delivery

    @database_sync_to_async
    def _load_history(self):
        from . import vault
        from .models import TelegraphChannel

        try:
            channel = TelegraphChannel.objects.get(slug=self.channel_slug)
        except TelegraphChannel.DoesNotExist:
            return []
        try:
            vault.purge_expired(channel)          # lazy purge on connect
            return vault.history(channel)
        except vault.VaultUnavailable:
            return []

    async def send_history(self):
        """Replay readable history to the just-connected socket."""
        try:
            messages = await self._load_history()
        except Exception:
            messages = []
        if not messages:
            return
        await self.send(text_data=json.dumps({
            "type": "chat_history",
            "room_slug": self.channel_slug,
            "messages": messages,
        }))

    async def broadcast_participants(self):
        await self.broadcast(
            payload=await self.channel_participants_payload(),
            sender_channel=self.channel_name,
            target_channel=None,
        )

    async def broadcast(self, *, payload, sender_channel, target_channel=None):
        await self.channel_layer.group_send(
            self.channel_group_name,
            {
                "type": "chat_message",
                "payload": payload,
                "sender_channel": sender_channel,
                "target_channel": target_channel,
            },
        )

    async def chat_message(self, event):
        target_channel = event.get("target_channel")

        if target_channel and target_channel != self.channel_name:
            return

        payload = event["payload"]

        # Do not echo local-only transport messages back to the socket that sent them.
        # MLS application messages cannot be decrypted by their sender.
        if (
            payload.get("type")
            in {
                "yjs_update",
                "yjs_sync_request",
                "mls_key_package",
                "mls_welcome",
                "mls_commit",
                "mls_app",
            }
            and event.get("sender_channel") == self.channel_name
        ):
            return

        await self.send(text_data=json.dumps(payload))

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
        from toto.telegraph.models import TelegraphChannel

        if not user or not user.is_authenticated:
            return False

        return TelegraphChannel.objects.filter(
            slug=self.channel_slug,
            telegraph_members__is_active=True,
            telegraph_members__person__user=user,
        ).exists()
