from django.urls import path

from . import consumers

websocket_urlpatterns = [
    path("ws/sabbia/agent/<slug:slug>/", consumers.AgentChatConsumer.as_asgi()),
]
