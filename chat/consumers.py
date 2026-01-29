import json
from channels.generic.websocket import AsyncWebsocketConsumer

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data=None, bytes_data=None):
        data = json.loads(text_data)

        # If encrypted message
        if "ciphertext" in data and "iv" in data:
            message_payload = data  # keep encrypted payload as-is
            message_text = "[encrypted]"  # optional placeholder for logs
        else:
            # Plaintext fallback
            message_payload = {"message": data["message"]}
            message_text = data["message"]

        user = self.scope["user"]
        username = user.username if user.is_authenticated else "Anonymous"

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "payload": message_payload,
                "user": username,
            }
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "user": event["user"],
            **event["payload"]
        }))

