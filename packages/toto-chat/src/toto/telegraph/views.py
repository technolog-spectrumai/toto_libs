from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, DetailView, View
from django.db import models
from toto.ui import PageProcessor
from toto.telegraph import permissions
from toto.telegraph.models import TelegraphMember, TelegraphChannel
from toto.people.models import Person


class ChannelListView(LoginRequiredMixin, ListView):
    model = TelegraphChannel
    template_name = "telegraph/channel_list.html"
    context_object_name = "channels"
    paginate_by = 20
    ordering = ["name"]

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            member_count=models.Count(
                "telegraph_members",
                filter=models.Q(telegraph_members__is_active=True),
                distinct=True,
            )
        )
        query = self.request.GET.get("q")
        if query:
            qs = qs.filter(models.Q(name__icontains=query) | models.Q(slug__icontains=query))
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        joined = set(
            permissions.readable_channels(self.request.user).values_list("pk", flat=True)
        )
        for channel in context["channels"]:
            channel.is_joined = channel.pk in joined
        return PageProcessor().decorate(context, self.request)


class ChannelDetailView(LoginRequiredMixin, DetailView):
    model = TelegraphChannel
    template_name = "telegraph/channel_details.html"
    context_object_name = "channel"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        channel = self.get_object()
        current_person = permissions.person_for(self.request.user)
        current_member = permissions.member_for(self.request.user, channel)

        context["all_channels"] = TelegraphChannel.objects.annotate(
            member_count=models.Count(
                "telegraph_members",
                filter=models.Q(telegraph_members__is_active=True),
                distinct=True,
            )
        )

        # The roster is only disclosed to members (permissions.py D6).
        if current_member:
            members_qs = channel.telegraph_members.filter(is_active=True).select_related("person")
            context["participants"] = [
                {
                    "username": m.display_name,
                    "avatar_url": m.avatar_url,
                    "type": m.participant_type,
                }
                for m in members_qs
            ]
        else:
            context["participants"] = []

        context["current_chat_user"] = (
            current_member.display_name
            if current_member
            else current_person.full_name
            if current_person
            else (self.request.user.get_full_name() or self.request.user.username)
        )
        context["current_chat_avatar_url"] = (
            current_member.avatar_url
            if current_member
            else "/static/img/avatars/default.png"
        )

        # History is delivered over the websocket, never server-rendered.
        context["messages"] = []
        context["current_person"] = current_person

        context["can_send_messages"] = current_member is not None
        context["can_join"] = bool(current_person and not current_member)
        context["can_leave"] = current_member is not None

        if not context["can_send_messages"]:
            if context["can_join"]:
                context["observer_reason"] = "Join this channel to read and send messages."
            elif not current_person:
                context["observer_reason"] = (
                    "You are observing because your user is not linked to a person profile."
                )
            else:
                context["observer_reason"] = "You are observing this channel."
        else:
            context["observer_reason"] = ""

        return PageProcessor().decorate(context, self.request)


class ChannelJoinView(LoginRequiredMixin, View):
    def post(self, request, slug):
        channel = get_object_or_404(TelegraphChannel, slug=slug)

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

        msg = f"You {'joined' if created else 'rejoined'} {channel.name} as a member."
        messages.success(request, msg)
        return redirect("telegraph:channel_detail", slug=channel.slug)


class ChannelLeaveView(LoginRequiredMixin, View):
    def post(self, request, slug):
        channel = get_object_or_404(TelegraphChannel, slug=slug)

        person = Person.objects.filter(user=request.user).first()
        if person:
            TelegraphMember.objects.filter(channel=channel, person=person, is_active=True).update(is_active=False)

        messages.success(request, f"You left {channel.name}.")
        return redirect("telegraph:channel_detail", slug=channel.slug)


class ChannelCreateView(LoginRequiredMixin, View):
    """Create a channel from the channel-list page and join it.

    Until now the only ways to create a channel were the Django admin and a seed
    command, which made the app unusable without operator access.
    """

    def post(self, request):
        from django.utils.text import slugify

        name = (request.POST.get("name") or "").strip()
        if not name:
            messages.error(request, "A channel needs a name.")
            return redirect("telegraph:channel_list")

        slug = slugify(name)[:50]
        if not slug:
            messages.error(request, "That name cannot be turned into a URL slug.")
            return redirect("telegraph:channel_list")

        if TelegraphChannel.objects.filter(models.Q(name=name) | models.Q(slug=slug)).exists():
            messages.error(request, f"A channel called “{name}” already exists.")
            return redirect("telegraph:channel_list")

        channel = TelegraphChannel.objects.create(
            name=name, slug=slug, created_by=request.user
        )
        person = Person.objects.filter(user=request.user).first()
        if person:
            TelegraphMember.objects.create(channel=channel, person=person, is_active=True)

        messages.success(request, f"Created {channel.name}.")
        return redirect("telegraph:channel_detail", slug=channel.slug)


class MessageSearchView(LoginRequiredMixin, ListView):
    """Full-text search across the messages the requester is allowed to read."""

    template_name = "telegraph/search.html"
    context_object_name = "results"
    paginate_by = 25

    def get_queryset(self):
        from .search import search_messages

        query = (self.request.GET.get("q") or "").strip()
        slug = (self.request.GET.get("channel") or "").strip()
        if not query:
            from .models import TelegraphMessage

            return TelegraphMessage.objects.none()
        return search_messages(self.request.user, query, channel_slug=slug or None)

    def get_context_data(self, **kwargs):
        from .search import search_mode

        context = super().get_context_data(**kwargs)
        context["query"] = (self.request.GET.get("q") or "").strip()
        context["channel_slug"] = (self.request.GET.get("channel") or "").strip()
        context["searchable_channels"] = permissions.readable_channels(self.request.user)
        context.update(search_mode())
        return PageProcessor().decorate(context, self.request)
