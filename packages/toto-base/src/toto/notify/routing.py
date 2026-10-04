"""The live socket's route (2026-10-04): one endpoint, ``ws/live/``.

A host's ASGI application takes ``websocket_urlpatterns`` as it stands
(zenobia: ``zenobia/routing.py``). Importing this module imports channels,
so only a host that serves sockets does it; the path itself is in
``toto.notify.ws`` for everything that only needs to name it.
"""

from django.urls import path

from .consumers import LiveConsumer
from .ws import WS_PATH

websocket_urlpatterns = [
    path(WS_PATH, LiveConsumer.as_asgi()),
]
