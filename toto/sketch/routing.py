from django.urls import path
from .consumer import BoardConsumer


websocket_urlpatterns = [
    path("ws/sketch/<str:board_id>/", BoardConsumer.as_asgi()),
]
