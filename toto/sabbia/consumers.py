import json

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

MAX_CONTENT_CHARS = 8000


class AgentChatConsumer(AsyncWebsocketConsumer):
    """1:1 user↔agent chat over websockets.

    Single-shot replies: each user message is forwarded to the agent's HTTP endpoint
    and the full assistant reply is sent back. Logged-in users only.

    Protocol:
        client → {"type": "user_message", "content": "..."}
        server → {"type": "ack", "conversation_id": int, "agent": str}
                 {"type": "typing", "state": bool}
                 {"type": "assistant_message", "content": str, "id": int, "created_at": iso}
                 {"type": "error", "message": str}
    """

    async def connect(self):
        self.slug = self.scope["url_route"]["kwargs"]["slug"]
        user = self.scope.get("user")
        if not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        self.agent = await self._load_agent(self.slug)
        if self.agent is None:
            await self.close(code=4404)
            return

        from toto.sabbia.endpoints import get_endpoint

        try:
            self.endpoint = get_endpoint(self.agent)
        except Exception:
            await self.close(code=4500)
            return

        self.conversation = await self._create_conversation(self.agent, user)
        await self.accept()
        await self.send(text_data=json.dumps({
            "type": "ack",
            "conversation_id": self.conversation.id,
            "agent": self.agent.name,
        }))

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            await self._error("Invalid JSON.")
            return

        if data.get("type") != "user_message" or not (data.get("content") or "").strip():
            await self._error("Expected {type:'user_message', content:'…'}.")
            return

        content = data["content"].strip()[:MAX_CONTENT_CHARS]
        await self._save_message("user", content)
        await self.send(text_data=json.dumps({"type": "typing", "state": True}))

        history = await self._build_history()
        try:
            reply = await sync_to_async(self.endpoint.chat, thread_sensitive=False)(history)
        except Exception as exc:
            await self.send(text_data=json.dumps({"type": "typing", "state": False}))
            await self._error(f"Agent error: {exc}")
            return

        row = await self._save_message("assistant", reply)
        await self.send(text_data=json.dumps({"type": "typing", "state": False}))
        await self.send(text_data=json.dumps({
            "type": "assistant_message",
            "content": reply,
            "id": row.id,
            "created_at": row.created_at.isoformat(),
        }))

    async def _error(self, message):
        await self.send(text_data=json.dumps({"type": "error", "message": message}))

    @database_sync_to_async
    def _load_agent(self, slug):
        from toto.sabbia.models import Agent

        return (
            Agent.objects.select_related("connector")
            .filter(slug=slug, is_active=True)
            .first()
        )

    @database_sync_to_async
    def _create_conversation(self, agent, user):
        from toto.sabbia.models import Conversation

        return Conversation.objects.create(agent=agent, user=user)

    @database_sync_to_async
    def _save_message(self, role, content):
        from toto.sabbia.models import ChatMessage

        return ChatMessage.objects.create(
            conversation=self.conversation, role=role, content=content
        )

    @database_sync_to_async
    def _build_history(self):
        messages = [{"role": "system", "content": self.agent.system_prompt}]
        for m in self.conversation.messages.all():
            messages.append({"role": m.role, "content": m.content})
        return messages
