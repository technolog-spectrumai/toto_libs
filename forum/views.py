# forum/views.py
from django.shortcuts import render, get_object_or_404, redirect
from .models import Room, Message
from .forms import MessageForm
from .page import PageProcessor
from collections import defaultdict


def room_list(request):
    """Display a list of all rooms."""
    rooms = Room.objects.all()
    context = {'rooms': rooms }
    return render(request, 'forum/room_list.html', PageProcessor().decorate(context, request))


def room_view(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    all_messages = room.messages.select_related('user', 'parent').order_by('timestamp')

    # Group messages into roots and replies
    roots = []
    replies_by_parent = {}

    for msg in all_messages:
        if msg.parent_id:
            replies_by_parent.setdefault(msg.parent_id, []).append(msg)
        else:
            roots.append(msg)

    # Attach replies to each root message using a safe temporary attribute
    for root in roots:
        setattr(root, '_replies', replies_by_parent.get(root.id, []))

    # Handle message form submission
    form = MessageForm()
    if request.method == 'POST':
        form = MessageForm(request.POST)
        if form.is_valid():
            content = form.cleaned_data['content']
            parent_id = request.POST.get('parent')
            parent = Message.objects.filter(id=parent_id).first() if parent_id else None
            room.post(content, parent=parent, user=request.user if request.user.is_authenticated else None)
            return redirect('forum:room_view', room_id=room.id)

    context = {
        'room': room,
        'root_messages': roots,
        'form': form
    }
    return render(request, 'forum/room_view.html', PageProcessor().decorate(context, request))


