from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, DetailView, View
from django.db import models
from toto.ui import PageProcessor
from toto.telegraph.models import TelegraphMember, TelegraphChannel
from toto.people.models import Person


class ChannelListView(ListView):
    model = TelegraphChannel
    template_name = "telegraph/channel_list.html"
    context_object_name = "channels"
    paginate_by = 20
    ordering = ["name"]

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            member_count=models.Count("telegraph_members", distinct=True)
        )
        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(models.Q(name__icontains=query) | models.Q(slug__icontains=query))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        return PageProcessor().decorate(context, self.request)


class ChannelDetailView(DetailView):
    model = TelegraphChannel
    template_name = "telegraph/channel_details.html"
    context_object_name = "channel"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_current_person(self):
        user = self.request.user
        if not user.is_authenticated:
            return None
        return Person.objects.filter(user=user).first()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        channel = self.get_object()
        current_person = self.get_current_person()

        context["all_channels"] = TelegraphChannel.objects.annotate(
            member_count=models.Count("telegraph_members", distinct=True)
        )

        members_qs = channel.telegraph_members.filter(is_active=True).select_related("person")
        current_member = members_qs.filter(person=current_person).first() if current_person else None

        context["participants"] = [
            {
                "username": m.display_name,
                "avatar_url": m.avatar_url,
                "type": m.participant_type,
            }
            for m in members_qs
        ]

        context["human_count"] = len(context["participants"])
        context["ai_count"] = 0

        context["current_chat_user"] = (
            current_member.display_name
            if current_member
            else current_person.full_name
            if current_person
            else (self.request.user.get_full_name() or self.request.user.username)
            if self.request.user.is_authenticated
            else "Guest"
        )
        context["current_chat_avatar_url"] = (
            current_member.avatar_url
            if current_member
            else "/static/img/avatars/default.png"
        )

        context["messages"] = []
        context["current_person"] = current_person

        context["is_joined"] = bool(
            self.request.user.is_authenticated
            and channel.participants.filter(pk=self.request.user.pk).exists()
        )
        context["has_active_member"] = bool(
            current_person and members_qs.filter(person=current_person).exists()
        )
        context["can_send_messages"] = context["is_joined"] and context["has_active_member"]
        context["can_join"] = bool(
            self.request.user.is_authenticated and current_person and not context["can_send_messages"]
        )
        context["can_leave"] = context["can_send_messages"]

        if not context["can_send_messages"]:
            if context["can_join"]:
                context["observer_reason"] = "Join this channel to become a member and send messages."
            elif not self.request.user.is_authenticated:
                context["observer_reason"] = "You are observing. Sign in with a person profile to join."
            elif not current_person:
                context["observer_reason"] = "You are observing because your user is not linked to a person profile."
            else:
                context["observer_reason"] = "You are observing this channel."
        else:
            context["observer_reason"] = ""

        context["available_agents"] = []

        return PageProcessor().decorate(context, self.request)


class ChannelJoinView(View):
    def post(self, request, slug):
        channel = get_object_or_404(TelegraphChannel, slug=slug)
        if not request.user.is_authenticated:
            messages.error(request, "Sign in to join this channel.")
            return redirect("telegraph:channel_detail", slug=channel.slug)

        person = Person.objects.filter(user=request.user).first()
        if not person:
            messages.error(request, "Your user is not linked to a person profile, so you can only observe this channel.")
            return redirect("telegraph:channel_detail", slug=channel.slug)

        member, created = TelegraphMember.objects.get_or_create(
            channel=channel, person=person, defaults={"is_active": True}
        )
        if not member.is_active:
            member.is_active = True
            member.save(update_fields=["is_active"])

        channel.participants.add(request.user)
        msg = f"You {'joined' if created else 'rejoined'} {channel.name} as a member."
        messages.success(request, msg)
        return redirect("telegraph:channel_detail", slug=channel.slug)


class ChannelLeaveView(View):
    def post(self, request, slug):
        channel = get_object_or_404(TelegraphChannel, slug=slug)
        if not request.user.is_authenticated:
            messages.error(request, "Sign in to leave this channel.")
            return redirect("telegraph:channel_detail", slug=channel.slug)

        person = Person.objects.filter(user=request.user).first()
        channel.participants.remove(request.user)
        if person:
            TelegraphMember.objects.filter(channel=channel, person=person, is_active=True).update(is_active=False)

        messages.success(request, f"You left {channel.name}.")
        return redirect("telegraph:channel_detail", slug=channel.slug)


class ChannelInviteAgentView(View):
    """Stub — agent members have been removed. Redirects back to the channel."""

    def get(self, request, slug):
        return redirect("telegraph:channel_detail", slug=slug)

    def post(self, request, slug):
        messages.error(request, "Agent members are no longer supported.")
        return redirect("telegraph:channel_detail", slug=slug)
