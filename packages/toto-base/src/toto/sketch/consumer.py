from channels.generic.websocket import AsyncWebsocketConsumer
import json
from asgiref.sync import sync_to_async


class BoardConsumer(AsyncWebsocketConsumer):

    async def connect(self):
        self.board_id = self.scope["url_route"]["kwargs"]["board_id"]
        self.group_name = f"board_{self.board_id}"

        if not await self.can_access_board():
            await self.close()
            return

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        # Broadcast to others
        await self.channel_layer.group_send(
            self.group_name,
            {
                "type": "broadcast",
                "data": data,
                "sender_channel": self.channel_name,
                "target_channel": data.get("target_channel"),
            }
        )

    async def broadcast(self, event):
        target_channel = event.get("target_channel")
        if event.get("sender_channel") == self.channel_name:
            return
        if target_channel and target_channel != self.channel_name:
            return

        data = event["data"]
        if data.get("type") == "yjs_sync_request":
            data = {
                **data,
                "sender_channel": event.get("sender_channel"),
            }

        await self.send(text_data=json.dumps(data))

    async def can_access_board(self):
        from toto.sketch.models import Board

        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            return False

        @sync_to_async
        def _exists():
            return Board.objects.filter(id=self.board_id, owner=user).exists()

        return await _exists()

    # async def save_board(self, objects):
    #     # Import models HERE, not at module level
    #     from toto.sketch.models import Board, BoardObject
    #
    #     @sync_to_async
    #     def _save():
    #         board = Board.objects.get(id=self.board_id)
    #
    #         incoming_ids = {obj["object_id"] for obj in objects}
    #
    #         # Soft delete missing objects
    #         BoardObject.objects.filter(board=board).exclude(
    #             object_id__in=incoming_ids
    #         ).update(is_deleted=True)
    #
    #         # Upsert objects
    #         for obj in objects:
    #             BoardObject.objects.update_or_create(
    #                 board=board,
    #                 object_id=obj["object_id"],
    #                 defaults={
    #                     "object_type": obj["object_type"],
    #                     "data": obj["data"],
    #                     "is_deleted": False,
    #                 }
    #             )
    #
    #     await _save()
