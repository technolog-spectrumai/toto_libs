from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path("ws/telegraph/<slug:channel_slug>/", consumers.ChatConsumer.as_asgi()),
]
