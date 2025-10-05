# forum/views.py
from django.shortcuts import render, get_object_or_404, redirect
from .models import Room, Message
from .forms import MessageForm
from .page import PageProcessor


def room_list(request):
    """Display a list of all rooms."""
    rooms = Room.objects.all()
    context = {'rooms': rooms }
    return render(request, 'forum/room_list.html', PageProcessor().decorate(context, request))


def room_view(request, room_id):
    """Display messages in a room and handle new posts."""
    room = get_object_or_404(Room, id=room_id)
    messages = room.messages.order_by('timestamp')
    form = MessageForm()

    if request.method == 'POST':
        form = MessageForm(request.POST)
        if form.is_valid():
            content = form.cleaned_data['content']
            parent_id = request.POST.get('parent')
            parent = Message.objects.filter(id=parent_id).first() if parent_id else None
            room.post(content, parent=parent)
            return redirect('forum:room_view', room_id=room.id)
    context = {
        'room': room,
        'messages': messages,
        'form': form
    }
    return render(request, 'forum/room_view.html', PageProcessor().decorate(context, request))
