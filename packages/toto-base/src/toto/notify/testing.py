"""Helpers for the notify and live-socket tests."""

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

MEMORY_LAYER = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


class Listener:
    """A channel in a group of the in-memory layer: what was published."""

    def __init__(self, group):
        self.layer = get_channel_layer()
        self.channel = async_to_sync(self.layer.new_channel)()
        async_to_sync(self.layer.group_add)(group, self.channel)

    def messages(self):
        found = []
        queue = self.layer.channels.get(self.channel)
        while queue is not None and not queue.empty():
            found.append(queue.get_nowait()[1])
        return found
