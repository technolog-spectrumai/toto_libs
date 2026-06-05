import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from diff_match_patch import diff_match_patch


class BaseFileSyncConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer that syncs a VaultFile's content across sessions.
    Subclasses must set room_prefix to a unique string (e.g. "editor_file").
    """
    room_prefix: str = "editor_file"

    async def connect(self):
        self.file_pk = self.scope["url_route"]["kwargs"]["file_pk"]
        self.room = f"{self.room_prefix}_{self.file_pk}"
        self.dmp = diff_match_patch()
        await self.channel_layer.group_add(self.room, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.room, self.channel_name)

    @database_sync_to_async
    def read_file(self) -> str:
        from toto.vault.models import VaultFile
        vf = VaultFile.objects.get(pk=self.file_pk)
        with vf.file.open("r") as f:
            return f.read()

    @database_sync_to_async
    def write_file(self, content: str) -> None:
        from toto.vault.models import VaultFile
        vf = VaultFile.objects.get(pk=self.file_pk)
        with vf.file.open("w") as f:
            f.write(content)

    async def receive(self, text_data):
        data = json.loads(text_data)
        incoming_content = data.get("content", "")
        incoming_patch = data.get("patch", "")
        msg_type = data.get("type", "full")

        current_content = await self.read_file()

        if incoming_patch:
            patches = self.dmp.patch_fromText(incoming_patch)
            new_content, _ = self.dmp.patch_apply(patches, current_content)
        else:
            new_content = incoming_content

        await self.write_file(new_content)

        await self.channel_layer.group_send(
            self.room,
            {
                "type": "sync_message",
                "sender": self.channel_name,
                "msg_type": msg_type,
                "content": new_content,
                "patch": incoming_patch,
            },
        )

    async def sync_message(self, event):
        if event["sender"] == self.channel_name:
            return
        await self.send(text_data=json.dumps({
            "type": event["msg_type"],
            "content": event["content"],
            "patch": event["patch"],
        }))


class EditorFileSyncConsumer(BaseFileSyncConsumer):
    room_prefix = "editor_file"
