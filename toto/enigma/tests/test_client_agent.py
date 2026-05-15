# studio/enigma/tests/test_agent_client_simple.py

import asyncio
import base64
import json
import os
import uuid
from contextlib import contextmanager

from django.test import TransactionTestCase

from toto.enigma.agent.connection import EnigmaConnection
from toto.enigma.agent.echo_client import EchoAgentWebsocketClient
from toto.enigma.models import Participant, Room


def b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


@contextmanager
def allow_async_unsafe_for_test():
    """
    Test-only helper.

    This fake websocket test intentionally runs async handler code in the same
    thread as Django ORM code. There is no real async server or concurrent DB
    access here.

    Do not use this in production.
    """
    old_value = os.environ.get("DJANGO_ALLOW_ASYNC_UNSAFE")
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

    try:
        yield
    finally:
        if old_value is None:
            os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        else:
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = old_value


class FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send(self, payload):
        self.sent.append(json.loads(payload))


class SyncTestEchoAgentWebsocketClient(EchoAgentWebsocketClient):
    """
    Test-only subclass.

    Production EchoAgentWebsocketClient should use database_sync_to_async.
    This subclass keeps DB access in the same thread for this simple test.
    """

    async def connection_receive(self, *, message_id: str, ciphertext: bytes):
        return self.connection.receive(
            message_id=message_id,
            ciphertext=ciphertext,
        )

    async def connection_encrypt(self, plaintext: bytes) -> bytes:
        return self.connection.encrypt(plaintext)

    async def connection_join_from_welcome(self, *, welcome: bytes):
        return self.connection.join_from_welcome(welcome=welcome)


class AgentClientSimpleTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.room = Room.objects.create(
            name=f"Test Room {uuid.uuid4()}",
            slug=f"test-room-{uuid.uuid4()}",
        )

        self.alice_agent = self.make_agent(
            name="Alice Agent",
            slug=f"alice-agent-{uuid.uuid4()}",
        )

        self.bob_agent = self.make_agent(
            name="Bob Agent",
            slug=f"bob-agent-{uuid.uuid4()}",
        )

        self.alice_participant = Participant.objects.create(
            room=self.room,
            agent=self.alice_agent,
            is_active=True,
        )

        self.bob_participant = Participant.objects.create(
            room=self.room,
            agent=self.bob_agent,
            is_active=True,
        )

    def make_agent(self, *, name, slug):
        from toto.steven.models import AgentProfile

        return AgentProfile.objects.create(
            name=name,
            slug=slug,
        )

    def test_echo_agent_handles_mls_app_message_and_sends_encrypted_reply(self):
        creator = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.alice_agent,
        )

        joiner = EnigmaConnection.create_joining_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        key_package = joiner.create_key_package()

        add_result = creator.add_member(
            key_package=bytes(key_package.public_key_package),
        )

        joiner.join_from_welcome(
            welcome=add_result.welcome,
            key_package=key_package,
        )

        ciphertext = creator.encrypt(b"hello bob")

        client = SyncTestEchoAgentWebsocketClient(
            websocket_url="ws://unused",
            connection=joiner,
            participant_id=self.bob_participant.id,
        )

        fake_ws = FakeWebSocket()

        with allow_async_unsafe_for_test():
            asyncio.run(
                client.handle_app(
                    fake_ws,
                    {
                        "type": "mls_app",
                        "message_id": "msg-1",
                        "sender_id": self.alice_participant.id,
                        "ciphertext": b64encode(ciphertext),
                    },
                )
            )

        self.assertEqual(len(fake_ws.sent), 1)

        reply_payload = fake_ws.sent[0]

        self.assertEqual(reply_payload["type"], "mls_app")
        self.assertEqual(
            str(reply_payload["sender_id"]),
            str(self.bob_participant.id),
        )
        self.assertIn("message_id", reply_payload)
        self.assertIn("ciphertext", reply_payload)

        received = creator.receive(
            message_id=reply_payload["message_id"],
            ciphertext=b64decode(reply_payload["ciphertext"]),
        )

        self.assertIsNotNone(received)
        self.assertEqual(
            received.plaintext,
            b"Agent received: hello bob",
        )

    def test_echo_agent_ignores_own_message(self):
        joiner = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        client = SyncTestEchoAgentWebsocketClient(
            websocket_url="ws://unused",
            connection=joiner,
            participant_id=self.bob_participant.id,
        )

        fake_ws = FakeWebSocket()

        with allow_async_unsafe_for_test():
            asyncio.run(
                client.handle_app(
                    fake_ws,
                    {
                        "type": "mls_app",
                        "message_id": "own-msg",
                        "sender_id": self.bob_participant.id,
                        "ciphertext": b64encode(b"not-real-ciphertext"),
                    },
                )
            )

        self.assertEqual(fake_ws.sent, [])