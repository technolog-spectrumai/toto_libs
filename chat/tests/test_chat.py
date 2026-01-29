import pytest
from channels.testing import WebsocketCommunicator
from toto.asgi import application
from .models import Room
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.asyncio
@pytest.mark.django_db
async def test_chat_websocket_connect_and_send_message():
    # Create test room + user
    room = Room.objects.create(name="general")
    user = User.objects.create_user(username="tester", password="pass123")

    # Create communicator for the WebSocket
    communicator = WebsocketCommunicator(
        application,
        "/ws/chat/general/"
    )

    # Force authentication (Channels 4.x)
    communicator.scope["user"] = user

    connected, _ = await communicator.connect()
    assert connected, "WebSocket failed to connect"

    # Send a message
    await communicator.send_json_to({"message": "Hello world"})

    # Receive broadcast
    response = await communicator.receive_json_from()
    assert response["message"] == "Hello world"
    assert response["username"] == "tester"

    await communicator.disconnect()
