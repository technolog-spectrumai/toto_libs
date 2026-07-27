"""Websocket consumer tests: membership gating, persistence, history replay, edit,
delete and typing.

Both hosts' ``scripts/reset.py`` run this module as their websocket smoke test.
"""
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase, override_settings

from toto.forum import store
from toto.forum.consumers import ChatConsumer
from toto.forum.models import ForumChannel, ForumMember, ForumMessage

User = get_user_model()

# An in-memory channel layer keeps the suite free of a Redis dependency.
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


def _communicator(slug, user):
    communicator = WebsocketCommunicator(
        ChatConsumer.as_asgi(), f"/ws/forum/{slug}/"
    )
    communicator.scope["url_route"] = {"kwargs": {"channel_slug": slug}}
    communicator.scope["user"] = user
    communicator.scope["headers"] = []
    return communicator


@override_settings(CHANNEL_LAYERS=CHANNEL_LAYERS)
class ChatConsumerTests(TransactionTestCase):
    def setUp(self):
        from toto.people.models import Person

        self.user = User.objects.create_user(username="wsuser", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="WS User")
        self.channel = ForumChannel.objects.create(name="Socket", slug="socket")
        self.member = ForumMember.objects.create(
            channel=self.channel, person=self.person, is_active=True
        )

    async def _connect(self, user=None):
        communicator = _communicator(self.channel.slug, user or self.user)
        connected, _ = await communicator.connect()
        return communicator, connected

    async def _drain_until(self, communicator, msg_type, tries=6):
        """Read frames until one of ``msg_type`` shows up (or give up)."""
        for _ in range(tries):
            frame = await communicator.receive_json_from(timeout=3)
            if frame.get("type") == msg_type:
                return frame
        return None

    # ── membership gating ─────────────────────────────────────────────────────
    async def test_non_member_is_rejected(self):
        outsider = await database_sync_to_async(User.objects.create_user)(
            username="outsider", password="pass"
        )
        communicator = _communicator(self.channel.slug, outsider)
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    async def test_anonymous_is_rejected(self):
        from django.contrib.auth.models import AnonymousUser

        communicator = _communicator(self.channel.slug, AnonymousUser())
        connected, code = await communicator.connect()
        self.assertFalse(connected)
        self.assertEqual(code, 4403)

    # ── history + send ────────────────────────────────────────────────────────
    async def test_history_is_replayed_on_connect(self):
        await database_sync_to_async(store.store_message)(
            self.channel, msg_type="chat_message", body="earlier", sender_name="WS User"
        )
        communicator, connected = await self._connect()
        self.assertTrue(connected)
        frame = await communicator.receive_json_from(timeout=3)
        self.assertEqual(frame["type"], "chat_history")
        self.assertEqual([m["message"] for m in frame["messages"]], ["earlier"])
        self.assertFalse(frame["has_more"])
        await communicator.disconnect()

    async def test_message_is_broadcast_and_persisted(self):
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)  # chat_history
        await communicator.send_json_to({"type": "chat_message", "message": "hello there"})

        frame = await self._drain_until(communicator, "chat_message")
        self.assertIsNotNone(frame)
        self.assertEqual(frame["message"], "hello there")
        self.assertEqual(frame["user"], "WS User")
        self.assertTrue(frame["id"])

        stored = await database_sync_to_async(
            lambda: list(ForumMessage.objects.values_list("body", flat=True))
        )()
        self.assertEqual(stored, ["hello there"])
        await communicator.disconnect()

    async def test_reply_to_is_persisted(self):
        parent = await database_sync_to_async(store.store_message)(
            self.channel, msg_type="chat_message", body="question"
        )
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        await communicator.send_json_to(
            {"type": "chat_message", "message": "answer", "reply_to": str(parent.id)}
        )
        frame = await self._drain_until(communicator, "chat_message")
        self.assertEqual(frame["reply_to"], str(parent.id))
        await communicator.disconnect()

    async def test_unknown_frame_type_is_rejected(self):
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        await communicator.send_json_to({"type": "mls_app", "ciphertext": "nope"})
        frame = await self._drain_until(communicator, "system_error")
        self.assertIsNotNone(frame)
        self.assertIn("Unsupported message type", frame["message"])
        await communicator.disconnect()

    # ── edit / delete ─────────────────────────────────────────────────────────
    async def test_author_can_edit_own_message(self):
        row = await database_sync_to_async(store.store_message)(
            self.channel, msg_type="chat_message", body="typo", sender=self.user
        )
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        await communicator.send_json_to(
            {"type": "message_edit", "id": str(row.id), "message": "fixed"}
        )
        frame = await self._drain_until(communicator, "message_edit")
        self.assertEqual(frame["message"], "fixed")
        self.assertIn("edited_at", frame)

        refreshed = await database_sync_to_async(ForumMessage.objects.get)(id=row.id)
        self.assertEqual(refreshed.body, "fixed")
        self.assertIsNotNone(refreshed.edited_at)
        await communicator.disconnect()

    async def test_cannot_edit_someone_elses_message(self):
        other = await database_sync_to_async(User.objects.create_user)(
            username="author2", password="pass"
        )
        row = await database_sync_to_async(store.store_message)(
            self.channel, msg_type="chat_message", body="theirs", sender=other
        )
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        await communicator.send_json_to(
            {"type": "message_edit", "id": str(row.id), "message": "hijacked"}
        )
        frame = await self._drain_until(communicator, "system_error")
        self.assertIsNotNone(frame)

        refreshed = await database_sync_to_async(ForumMessage.objects.get)(id=row.id)
        self.assertEqual(refreshed.body, "theirs")
        await communicator.disconnect()

    async def test_delete_is_soft_and_keeps_the_row(self):
        row = await database_sync_to_async(store.store_message)(
            self.channel, msg_type="chat_message", body="regret", sender=self.user
        )
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        await communicator.send_json_to({"type": "message_delete", "id": str(row.id)})
        frame = await self._drain_until(communicator, "message_delete")
        self.assertEqual(frame["id"], str(row.id))

        refreshed = await database_sync_to_async(ForumMessage.objects.get)(id=row.id)
        self.assertIsNotNone(refreshed.deleted_at)  # tombstone, not a hard delete
        await communicator.disconnect()

    # ── typing ────────────────────────────────────────────────────────────────
    async def test_typing_is_not_echoed_to_its_sender(self):
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)  # chat_history
        await communicator.send_json_to({"type": "typing_start"})

        # Drain whatever is pending (the connect-time room_participants frame) and assert
        # the sender's own typing frame is not among it.
        seen = []
        while not await communicator.receive_nothing(timeout=0.4):
            seen.append((await communicator.receive_json_from(timeout=1))["type"])
        self.assertNotIn("typing_start", seen)
        await communicator.disconnect()

    async def test_typing_reaches_another_member(self):
        from toto.people.models import Person

        other = await database_sync_to_async(User.objects.create_user)(
            username="watcher", password="pass"
        )
        other_person = await database_sync_to_async(Person.objects.create)(
            user=other, display_name="Watcher"
        )
        await database_sync_to_async(ForumMember.objects.create)(
            channel=self.channel, person=other_person, is_active=True
        )

        a, _ = await self._connect()
        b, _ = await self._connect(user=other)
        await a.receive_json_from(timeout=3)
        await b.receive_json_from(timeout=3)

        await a.send_json_to({"type": "typing_start"})
        frame = await self._drain_until(b, "typing_start")
        self.assertIsNotNone(frame)
        self.assertEqual(frame["user"], "WS User")

        await a.disconnect()
        await b.disconnect()

    # ── regression: a malformed id must not kill the socket ───────────────────
    async def test_non_uuid_message_id_does_not_kill_the_socket(self):
        """A bare string fed to a UUID pk filter raises ValidationError.

        Unhandled, that propagates out of the consumer and Channels tears the socket
        down, so the client silently loses live delivery until a page reload.
        """
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)  # chat_history

        for frame in (
            {"type": "message_delete", "id": "abc"},
            {"type": "message_edit", "id": "abc", "message": "x"},
            {"type": "chat_message", "message": "hi", "reply_to": "abc"},
        ):
            await communicator.send_json_to(frame)
            # Drain whatever comes back; the point is that the socket survives.
            while not await communicator.receive_nothing(timeout=0.6):
                await communicator.receive_json_from(timeout=1)

        # Still usable afterwards — proof the consumer was never torn down.
        await communicator.send_json_to({"type": "chat_message", "message": "still here"})
        frame = await self._drain_until(communicator, "chat_message")
        self.assertIsNotNone(frame)
        self.assertEqual(frame["message"], "still here")
        await communicator.disconnect()

    async def test_reply_to_another_channels_message_is_ignored(self):
        other = await database_sync_to_async(ForumChannel.objects.create)(
            name="Other", slug="other"
        )
        foreign = await database_sync_to_async(store.store_message)(
            other, msg_type="chat_message", body="elsewhere"
        )
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        await communicator.send_json_to(
            {"type": "chat_message", "message": "hi", "reply_to": str(foreign.id)}
        )
        frame = await self._drain_until(communicator, "chat_message")
        self.assertIsNotNone(frame)
        self.assertNotIn("reply_to", frame)  # scoped to this channel, so dropped
        await communicator.disconnect()

    # ── regression: a revoked member must stop receiving ──────────────────────
    async def test_revoked_member_stops_receiving(self):
        """Membership is checked at connect; leaving via REST does not close the tab's
        socket, so without a re-check an ex-member keeps receiving every message."""
        communicator, _ = await self._connect()
        await communicator.receive_json_from(timeout=3)
        while not await communicator.receive_nothing(timeout=0.5):
            await communicator.receive_json_from(timeout=1)

        # Exactly what the REST/HTML leave paths do: an instance save, which fires the
        # signal that reaches the live socket.
        @database_sync_to_async
        def revoke():
            self.member.is_active = False
            self.member.save(update_fields=["is_active"])

        await revoke()

        layer = get_channel_layer()
        await layer.group_send(
            f"forum_{self.channel.slug}",
            {
                "type": "chat_message",
                "payload": {"type": "chat_message", "id": "x", "message": "leak"},
                "sender_channel": None,
                "echo": True,
            },
        )

        # The revoked socket must not receive the message.
        leaked = []
        for _ in range(3):
            if await communicator.receive_nothing(timeout=0.5):
                continue
            try:
                leaked.append(await communicator.receive_json_from(timeout=1))
            except Exception:
                break
        self.assertEqual(
            [f for f in leaked if f.get("message") == "leak"], [],
            "an ex-member received a message posted after their membership was revoked",
        )
