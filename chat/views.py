from django.shortcuts import render, get_object_or_404
from django.contrib.auth.decorators import login_required
from oya.page import PageProcessor
from .models import Room


@login_required
def chat_view(request, room_name=None):
    processor = PageProcessor()

    rooms = Room.objects.all().order_by("name")

    # If no room specified → pick the first available
    if room_name is None:
        room = rooms.first() if rooms.exists() else None
    else:
        room = get_object_or_404(Room, name=room_name)

    # Participants
    participants = (
        room.allowed_users.all()
        if room and room.allowed_users.exists()
        else []
    )

    # Messages
    messages = room.messages.select_related("user").all() if room else []
    messages_json = [
        {
            "user": m.user.username if m.user else "Anonymous",
            "content": m.content,
            "timestamp": m.timestamp.strftime("%H:%M"),
        }
        for m in messages
    ]

    context = {
        "rooms": rooms,
        "room": room,
        "participants": participants,
        "messages_json": messages_json,
    }

    return render(
        request,
        "chat/chat.html",
        processor.decorate(context, request)
    )
