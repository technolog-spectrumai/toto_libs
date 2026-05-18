from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, DetailView, View
from django.db import models
from toto.ui import PageProcessor
from toto.enigma.models import Participant, Room
from toto.people.models import Person


class RoomListView(ListView):
    model = Room
    template_name = "enigma/room_list.html"
    context_object_name = "rooms"
    paginate_by = 20
    ordering = ["name"]

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            participant_count=models.Count("chat_participants", distinct=True)
        )
        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(models.Q(name__icontains=query) | models.Q(slug__icontains=query))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class RoomDetailView(DetailView):
    model = Room
    template_name = "enigma/room_details.html"
    context_object_name = "room"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_current_person(self):
        user = self.request.user
        if not user.is_authenticated:
            return None
        return Person.objects.filter(user=user).first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        room = self.get_object()
        current_person = self.get_current_person()

        # Sidebar: all rooms
        context["all_rooms"] = Room.objects.annotate(
            participant_count=models.Count("chat_participants", distinct=True)
        )

        # Active participants
        participants_qs = room.chat_participants.filter(is_active=True).select_related("person")
        current_chat_participant = participants_qs.filter(person=current_person).first() if current_person else None

        context["participants"] = [
            {
                "username": p.display_name,
                "avatar_url": p.avatar_url,
                "type": p.participant_type,
            }
            for p in participants_qs
        ]

        context["human_count"] = len(context["participants"])
        context["ai_count"] = 0

        # Current chat user info
        context["current_chat_user"] = (
            current_chat_participant.display_name
            if current_chat_participant
            else current_person.full_name
            if current_person
            else (self.request.user.get_full_name() or self.request.user.username)
            if self.request.user.is_authenticated
            else "Guest"
        )
        context["current_chat_avatar_url"] = (
            current_chat_participant.avatar_url
            if current_chat_participant
            else "/static/img/avatars/default.png"
        )

        context["messages"] = []
        context["current_person"] = current_person

        # Permission flags
        context["is_joined"] = bool(
            self.request.user.is_authenticated
            and room.participants.filter(pk=self.request.user.pk).exists()
        )
        context["has_active_participant"] = bool(
            current_person and participants_qs.filter(person=current_person).exists()
        )
        context["can_send_messages"] = context["is_joined"] and context["has_active_participant"]
        context["can_join"] = bool(
            self.request.user.is_authenticated and current_person and not context["can_send_messages"]
        )
        context["can_leave"] = context["can_send_messages"]

        # Observer reason text
        if not context["can_send_messages"]:
            if context["can_join"]:
                context["observer_reason"] = "Join this room to become a participant and send messages."
            elif not self.request.user.is_authenticated:
                context["observer_reason"] = "You are observing. Sign in with a person profile to join."
            elif not current_person:
                context["observer_reason"] = "You are observing because your user is not linked to a person profile."
            else:
                context["observer_reason"] = "You are observing this room."
        else:
            context["observer_reason"] = ""

        context["available_agents"] = []

        return PageProcessor().decorate(context, self.request)


class RoomJoinView(View):
    def post(self, request, slug):
        room = get_object_or_404(Room, slug=slug)
        if not request.user.is_authenticated:
            messages.error(request, "Sign in to join this room.")
            return redirect("enigma:room_detail", slug=room.slug)

        person = Person.objects.filter(user=request.user).first()
        if not person:
            messages.error(request, "Your user is not linked to a person profile, so you can only observe this room.")
            return redirect("enigma:room_detail", slug=room.slug)

        participant, created = Participant.objects.get_or_create(
            room=room, person=person, defaults={"is_active": True}
        )
        if not participant.is_active:
            participant.is_active = True
            participant.save(update_fields=["is_active"])

        room.participants.add(request.user)
        msg = f"You {'joined' if created else 'rejoined'} {room.name} as a participant."
        messages.success(request, msg)
        return redirect("enigma:room_detail", slug=room.slug)


class RoomLeaveView(View):
    def post(self, request, slug):
        room = get_object_or_404(Room, slug=slug)
        if not request.user.is_authenticated:
            messages.error(request, "Sign in to leave this room.")
            return redirect("enigma:room_detail", slug=room.slug)

        person = Person.objects.filter(user=request.user).first()
        room.participants.remove(request.user)
        if person:
            Participant.objects.filter(room=room, person=person, is_active=True).update(is_active=False)

        messages.success(request, f"You left {room.name}.")
        return redirect("enigma:room_detail", slug=room.slug)


class RoomInviteAgentView(View):
    """Stub — agent participants have been removed. Redirects back to the room."""

    def get(self, request, slug):
        return redirect("enigma:room_detail", slug=slug)

    def post(self, request, slug):
        messages.error(request, "Agent participants are no longer supported.")
        return redirect("enigma:room_detail", slug=slug)