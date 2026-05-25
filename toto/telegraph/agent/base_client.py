import base64
import json
import uuid
from abc import ABC, abstractmethod

import websockets
from channels.db import database_sync_to_async


def b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def b64decode(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"))


class BaseAgentWebsocketClient(ABC):
    def __init__(
        self,
        *,
        websocket_url: str,
        connection,
        participant_id: int,
        headers: dict | None = None,
    ):
        self.websocket_url = websocket_url
        self.connection = connection
        self.participant_id = participant_id
        self.headers = headers or {}

    async def run_forever(self):
        try:
            async with websockets.connect(
                self.websocket_url,
                additional_headers=self.headers,
            ) as ws:
                async for raw_message in ws:
                    await self.handle_message(ws, json.loads(raw_message))
        except TypeError:
            async with websockets.connect(
                self.websocket_url,
                extra_headers=self.headers,
            ) as ws:
                async for raw_message in ws:
                    await self.handle_message(ws, json.loads(raw_message))

    async def handle_message(self, ws, data: dict):
        if data.get("type") == "mls_welcome":
            await self.handle_welcome(data)
            return

        if data.get("type") == "mls_app":
            await self.handle_app(ws, data)
            return

    async def handle_welcome(self, data: dict):
        recipient_id = data.get("recipient_id")

        if recipient_id and str(recipient_id) != str(self.participant_id):
            return

        welcome = data.get("welcome")
        if not welcome:
            return

        await self.connection_join_from_welcome(
            welcome=b64decode(welcome),
        )

    async def handle_app(self, ws, data: dict):
        sender_id = data.get("sender_id")

        if sender_id and str(sender_id) == str(self.participant_id):
            return

        ciphertext = data.get("ciphertext")
        message_id = data.get("message_id")

        if not ciphertext or not message_id:
            return

        received = await self.connection_receive(
            message_id=message_id,
            ciphertext=b64decode(ciphertext),
        )

        if received is None:
            return

        response_text = await self.build_response(
            plaintext=received.plaintext,
            envelope=data,
        )

        if not response_text or not response_text.strip():
            return

        reply_ciphertext = await self.connection_encrypt(
            response_text.encode("utf-8"),
        )

        await ws.send(
            json.dumps(
                {
                    "type": "mls_app",
                    "message_id": f"agent-{uuid.uuid4()}",
                    "sender_id": self.participant_id,
                    "ciphertext": b64encode(reply_ciphertext),
                }
            )
        )

    @database_sync_to_async
    def connection_receive(self, *, message_id: str, ciphertext: bytes):
        return self.connection.receive(
            message_id=message_id,
            ciphertext=ciphertext,
        )

    @database_sync_to_async
    def connection_encrypt(self, plaintext: bytes) -> bytes:
        return self.connection.encrypt(plaintext)

    @database_sync_to_async
    def connection_join_from_welcome(self, *, welcome: bytes):
        return self.connection.join_from_welcome(welcome=welcome)

    @abstractmethod
    async def build_response(self, *, plaintext: bytes, envelope: dict) -> str:
        raise NotImplementedError
