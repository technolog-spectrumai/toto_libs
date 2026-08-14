from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, DetailView, View
from django.db import models
from toto.ui import PageProcessor
from toto.forum import permissions
from toto.forum.models import ForumMember, ForumChannel
from toto.people.models import Person


class ChannelListView(LoginRequiredMixin, ListView):
    model = ForumChannel
    template_name = "forum/channel_list.html"
    context_object_name = "channels"
    paginate_by = 20
    ordering = ["name"]

    def get_queryset(self):
        qs = super().get_queryset().annotate(
            member_count=models.Count(
                "forum_members",
                filter=models.Q(forum_members__is_active=True),
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
    model = ForumChannel
    template_name = "forum/channel_details.html"
    context_object_name = "channel"
    slug_field = "slug"
    slug_url_kwarg = "slug"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        channel = self.get_object()
        current_person = permissions.person_for(self.request.user)
        current_member = permissions.member_for(self.request.user, channel)

        context["all_channels"] = ForumChannel.objects.annotate(
            member_count=models.Count(
                "forum_members",
                filter=models.Q(forum_members__is_active=True),
                distinct=True,
            )
        )

        # The roster is only disclosed to members (permissions.py D6).
        if current_member:
            members_qs = channel.forum_members.filter(is_active=True).select_related("person")
            context["participants"] = [
                {
                    "username": m.display_name,
                    "avatar_url": m.avatar_url,
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

        # History is delivered over the websocket, never server-rendered. Note this is
        # deliberately NOT called "messages": that name is taken by the
        # django.contrib.messages context processor, and shadowing it silently swallowed
        # every flash message the join/leave/create redirects set.
        context["initial_messages"] = []
        context["current_person"] = current_person
        # The room tab strip: chat is one of four surfaces. Members only —
        # an observer sees the chat preview, not the room's library or polls.
        from django.apps import apps as django_apps

        context["active_tab"] = "chat"
        context["is_participant"] = current_member is not None
        context["polls_installed"] = django_apps.is_installed("toto.polls")

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
        channel = get_object_or_404(ForumChannel, slug=slug)

        person = Person.objects.filter(user=request.user).first()
        if not person:
            messages.error(request, "Your user is not linked to a person profile, so you can only observe this channel.")
            return redirect("forum:channel_detail", slug=channel.slug)

        member, created = ForumMember.objects.get_or_create(
            channel=channel, person=person, defaults={"is_active": True}
        )
        if not member.is_active:
            member.is_active = True
            member.save(update_fields=["is_active"])

        msg = f"You {'joined' if created else 'rejoined'} {channel.name} as a member."
        messages.success(request, msg)
        return redirect("forum:channel_detail", slug=channel.slug)


class ChannelLeaveView(LoginRequiredMixin, View):
    def post(self, request, slug):
        channel = get_object_or_404(ForumChannel, slug=slug)

        person = Person.objects.filter(user=request.user).first()
        if person:
            # Deactivate per instance, not with a queryset .update(): the latter fires
            # no signals, so signals.py would never tell a live socket it was revoked.
            for member in ForumMember.objects.filter(
                channel=channel, person=person, is_active=True
            ):
                member.is_active = False
                member.save(update_fields=["is_active"])

        messages.success(request, f"You left {channel.name}.")
        return redirect("forum:channel_detail", slug=channel.slug)


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
            return redirect("forum:channel_list")

        slug = slugify(name)[:50]
        if not slug:
            messages.error(request, "That name cannot be turned into a URL slug.")
            return redirect("forum:channel_list")

        if ForumChannel.objects.filter(models.Q(name=name) | models.Q(slug=slug)).exists():
            messages.error(request, f"A channel called “{name}” already exists.")
            return redirect("forum:channel_list")

        channel = ForumChannel.objects.create(
            name=name, slug=slug, created_by=request.user
        )
        person = Person.objects.filter(user=request.user).first()
        if person:
            ForumMember.objects.create(channel=channel, person=person, is_active=True)

        messages.success(request, f"Created {channel.name}.")
        return redirect("forum:channel_detail", slug=channel.slug)


class MessageSearchView(LoginRequiredMixin, ListView):
    """Full-text search across the messages the requester is allowed to read."""

    template_name = "forum/search.html"
    context_object_name = "results"
    paginate_by = 25

    def get_queryset(self):
        from .search import search_messages

        query = (self.request.GET.get("q") or "").strip()
        slug = (self.request.GET.get("channel") or "").strip()
        if not query:
            from .models import ForumMessage

            return ForumMessage.objects.none()
        return search_messages(self.request.user, query, channel_slug=slug or None)

    def get_context_data(self, **kwargs):
        from .search import search_mode

        context = super().get_context_data(**kwargs)
        context["query"] = (self.request.GET.get("q") or "").strip()
        context["channel_slug"] = (self.request.GET.get("channel") or "").strip()
        context["searchable_channels"] = permissions.readable_channels(self.request.user)
        context.update(search_mode())
        return PageProcessor().decorate(context, self.request)


# ---------------------------------------------------------------------------
# Room tabs — Files / Polls / Statistics. Chat stays the websocket page.
# Every one of these opens with permissions.require_member: the single door.
# ---------------------------------------------------------------------------

def _room_context(request, channel, active_tab):
    from django.apps import apps as django_apps

    context = {
        "channel": channel,
        "active_tab": active_tab,
        "polls_installed": django_apps.is_installed("toto.polls"),
    }
    return PageProcessor().decorate(context, request)


def room_files(request, slug):
    """The room's library: the vault's tree, scoped to this room's directory.

    The tab renders and links; uploading happens on the vault's own gateway
    page, where quota, charging and antivirus screening already live — this
    app carries no upload machinery (one door, the antivirus lesson).
    """
    from django.contrib.auth.decorators import login_required  # noqa: F401
    from django.shortcuts import render

    from toto.vault.filetree import build_file_tree

    from . import library

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)
    library.ensure_channel_library(channel)

    files = library.library_files(channel)
    total_bytes = files.aggregate(
        total=models.Sum("file_size_bytes"))["total"] or 0

    context = _room_context(request, channel, "files")
    context.update({
        "tree": build_file_tree(request.user, queryset=files),
        "file_count": files.count(),
        "total_mb": round(total_bytes / (1024 * 1024), 1),
        "gateway_dir_pk": channel.vault_directory_id,
    })
    return render(request, "forum/room_files.html", context)


def room_polls(request, slug):
    """The room's polls — engine objects, room UI. Nothing here counts."""
    from django.shortcuts import render

    from toto.polls import services as polls_services

    from . import voting

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)

    cards = []
    for question in voting.questions_for(channel).prefetch_related("choices"):
        roll = polls_services.electorate_for(question)
        counted = polls_services.tally(question, electorate=roll)
        cards.append({
            "question": question,
            "is_open": question.is_open,
            "tally": counted,
            "rows": [{"result": r,
                      "share_percent": (counted.share(r) * 100
                                        if counted.share(r) is not None
                                        else None)}
                     for r in counted.results],
            "ballot": polls_services.ballot_of(question, request.user),
        })

    context = _room_context(request, channel, "polls")
    context["cards"] = cards
    return render(request, "forum/room_polls.html", context)


def room_poll_create(request, slug):
    """POST from the Create Poll modal. Any active member."""
    from django.core.exceptions import ValidationError
    from django.utils import timezone as tz
    from django.utils.dateparse import parse_datetime
    from django.views.decorators.http import require_POST  # noqa: F401

    from . import voting

    if request.method != "POST":
        return redirect("forum:room_polls", slug=slug)
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)

    title = (request.POST.get("title") or "").strip()[:150]
    closes_raw = (request.POST.get("closes_at") or "").strip()
    closes_at = parse_datetime(closes_raw) if closes_raw else None
    if closes_at is not None and tz.is_naive(closes_at):
        closes_at = tz.make_aware(closes_at)

    if not title:
        messages.error(request, "A poll needs a question.")
    else:
        try:
            voting.open_room_poll(channel, request.user, title=title,
                                  options=request.POST.get("options", ""),
                                  closes_at=closes_at)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
        else:
            messages.success(request, "The poll is open.")
    return redirect("forum:room_polls", slug=slug)


def room_poll_vote(request, slug, question_slug):
    """POST one answer. The engine gates by the room electorate — a
    non-member is refused even if they somehow reach this door."""
    from toto.polls.core import VotingError
    from toto.polls.models import Choice

    from . import voting

    if request.method != "POST":
        return redirect("forum:room_polls", slug=slug)
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)
    question = get_object_or_404(voting.questions_for(channel),
                                 slug=question_slug)
    choice = get_object_or_404(Choice, pk=request.POST.get("choice") or 0,
                               question=question)

    from toto.polls import services as polls_services

    try:
        polls_services.cast(question, request.user, choice,
                            electorate=polls_services.electorate_for(question))
    except VotingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Your answer has been recorded.")
    return redirect("forum:room_polls", slug=slug)


def room_stats(request, slug):
    """The room in numbers. Fetch once, fold in Python — the kanban lesson:
    a query count must not be a function of the data."""
    import json
    from datetime import timedelta

    from django.db.models.functions import ExtractHour, TruncDate
    from django.shortcuts import render
    from django.utils import timezone as tz

    from . import library

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)

    visible = channel.messages.filter(deleted_at__isnull=True)
    files = library.library_files(channel)
    total_bytes = files.aggregate(total=models.Sum("file_size_bytes"))["total"] or 0

    # The trailing order_by() defeats the Meta-ordering GROUP BY trap:
    # ForumMessage orders by created_at, and Django folds an ORDER BY column
    # into the GROUP BY — one row per message instead of one per bucket.
    since = tz.now() - timedelta(days=30)
    by_day = dict(
        visible.filter(created_at__gte=since)
        .annotate(day=TruncDate("created_at"))
        .values_list("day").annotate(n=models.Count("id")).order_by("day"))
    days, day_counts = [], []
    for offset in range(29, -1, -1):
        day = (tz.now() - timedelta(days=offset)).date()
        days.append(day.isoformat())
        day_counts.append(by_day.get(day, 0))

    by_hour = dict(
        visible.annotate(hour=ExtractHour("created_at"))
        .values_list("hour").annotate(n=models.Count("id")).order_by("hour"))
    hour_counts = [by_hour.get(hour, 0) for hour in range(24)]

    context = _room_context(request, channel, "stats")
    context.update({
        "message_count": visible.count(),
        "active_members": channel.forum_members.filter(is_active=True).count(),
        "file_count": files.count(),
        "total_mb": round(total_bytes / (1024 * 1024), 1),
        "day_chart_json": json.dumps({
            "chart_type": "bar",
            "labels": days,
            "datasets": [{"label": "Messages", "data": day_counts,
                          "backgroundColor": "#4F46E5"}],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        }) if sum(day_counts) else "",
        "hour_chart_json": json.dumps({
            "chart_type": "bar",
            "labels": [f"{hour:02d}" for hour in range(24)],
            "datasets": [{"label": "Messages", "data": hour_counts,
                          "backgroundColor": "#10B981"}],
            "options": {"scales": {"y": {"beginAtZero": True}}},
        }) if sum(hour_counts) else "",
    })
    return render(request, "forum/room_stats.html", context)
