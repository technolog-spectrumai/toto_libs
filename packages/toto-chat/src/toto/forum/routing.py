from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path("ws/forum/<slug:channel_slug>/", consumers.ChatConsumer.as_asgi()),
]
