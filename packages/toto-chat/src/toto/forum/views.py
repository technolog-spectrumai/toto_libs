from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe
from django.views.generic import ListView, DetailView, View
from django.db import models
from django.utils.translation import gettext as _
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
        ids = permissions.listable_channels(self.request.user).values("pk")
        qs = super().get_queryset().filter(pk__in=ids).annotate(
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
        # Staff-only forum controls. Hidden rather than disabled: a link that
        # always answers 403 is worse than no link — and the page behind it
        # re-checks, because hiding is cosmetic.
        context["is_operator"] = permissions.is_operator(self.request.user)
        from . import creation

        context["expiry_choices"] = [
            ("", _("Never")), ("1h", _("1 hour")), ("24h", _("24 hours")),
            ("7d", _("7 days")), ("30d", _("30 days"))]
        context["min_password"] = creation.MIN_PASSWORD
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

        context["can_send_messages"] = current_member is not None and not channel.is_expired
        verdict = permissions.join_verdict(self.request.user, channel)
        context["join_verdict"] = verdict
        context["can_join"] = bool(current_person and not current_member
                                   and verdict in ("open", "password"))
        context["needs_password"] = verdict == "password"
        context["can_leave"] = current_member is not None
        context["badges"] = channel.badges()
        context["is_encrypted"] = channel.is_encrypted
        context["can_manage_members"] = permissions.can_manage_members(self.request.user, channel)
        context["forum_price_code"] = "forum.encrypt" if channel.is_encrypted else "forum.message"

        if not context["can_send_messages"]:
            if context["can_join"]:
                context["observer_reason"] = _("Join this channel to read and send messages.")
            elif not current_person:
                context["observer_reason"] = _(
                    "You are observing because your user is not linked to a person profile."
                )
            else:
                context["observer_reason"] = _("You are observing this channel.")
        else:
            context["observer_reason"] = ""

        return PageProcessor().decorate(context, self.request)


class ChannelJoinView(LoginRequiredMixin, View):
    def post(self, request, slug):
        from . import creation

        channel = get_object_or_404(ForumChannel, slug=slug)
        if not permissions.listable_channels(request.user).filter(pk=channel.pk).exists():
            raise Http404
        try:
            created = creation.join(request.user, channel,
                                    password=request.POST.get("password", ""))
        except creation.RoomRefused as exc:
            messages.error(request, str(exc))
            return redirect("forum:channel_detail", slug=channel.slug)
        messages.success(request, _("You joined %(name)s as a member.") % {"name": channel.name}
                         if created else _("You are a member of %(name)s.") % {"name": channel.name})
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
    """Create a room from the channel-list page and join it.

    Who may join (open / password), whether it is encrypted at rest,
    and whether it expires are chosen here and fixed for the room's life —
    except the password, which its creator may change. creation.create_room
    holds every rule; the API door calls the same function.
    """

    def post(self, request):
        from toto.quota.api import InArrears, QuotaExceeded
        from toto.quota.charge import InsufficientFunds

        from . import creation

        try:
            channel = creation.create_room(
                request.user, name=request.POST.get("name", ""),
                access=request.POST.get("access", "open"),
                password=request.POST.get("password", ""),
                encrypted=request.POST.get("encrypted") == "1",
                expires_in=request.POST.get("expires_in", ""))
        except creation.RoomRefused as exc:
            messages.error(request, str(exc))
            return redirect("forum:channel_list")
        except (QuotaExceeded, InArrears, InsufficientFunds) as exc:
            messages.error(request, str(exc))
            return redirect("forum:channel_list")
        messages.success(request, _("Created %(name)s.") % {"name": channel.name})
        return redirect("forum:channel_detail", slug=channel.slug)


def _writers(channel):
    """Everyone who has written in this room, most data first.

    One grouped query over the room's messages — every row still stored,
    soft-deleted ones included (they were written, and they still take
    space until the cleanup removes them). Data is what the room holds for
    each person: the text (or its ciphertext in an encrypted room) plus the
    files. Text is counted in characters, which is bytes for plain text and
    close enough for the rest.
    """
    from django.db.models import Count, F, Func, IntegerField, Sum, Value
    from django.db.models.functions import Coalesce, Length

    from toto.people.models import Person

    from .models import ForumMember, ForumMessage

    rows = list(
        ForumMessage.objects.filter(channel=channel, sender__isnull=False)
        .values("sender_id")
        .annotate(
            messages=Count("id"),
            files=Count("id", filter=~models.Q(attachment="") & models.Q(attachment__isnull=False)),
            text=Coalesce(Sum(Length("body")), Value(0)),
            sealed=Coalesce(Sum(Func(F("body_sealed"), function="LENGTH",
                                     output_field=IntegerField())), Value(0)),
            attached=Coalesce(Sum("attachment_size"), Value(0)),
            last_name=models.Max("sender_name"),
        )
        .order_by())
    user_ids = [r["sender_id"] for r in rows]
    people = {p.user_id: p for p in Person.objects.filter(user_id__in=user_ids).select_related("user")}
    active = {m.person.user_id: m for m in ForumMember.objects.filter(
        channel=channel, is_active=True, person__user_id__in=user_ids).select_related("person")}
    from toto.quota.rates import significant

    out = []
    for r in rows:
        person = people.get(r["sender_id"])
        total = int(r["text"] or 0) + int(r["sealed"] or 0) + int(r["attached"] or 0)
        out.append({
            "name": (person.display_name if person and person.display_name else r["last_name"]) or "?",
            "username": person.user.username if person and person.user_id else "",
            "user_id": r["sender_id"],
            "member": active.get(r["sender_id"]),
            "messages": r["messages"],
            "files": r["files"],
            "bytes": total,
            "mb": significant(total / (1024 * 1024)),
        })
    out.sort(key=lambda w: (-w["bytes"], w["name"].lower()))
    return out


def room_members(request, slug):
    """Who has written in the room and how much data each sent; for its
    creator or staff, remove a writer and set the room password.

    There are no invitations (2026-09-28): a password will do. People who
    joined and never wrote are counted, not listed.
    """
    from django.contrib.auth.views import redirect_to_login

    from . import creation

    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())
    channel = get_object_or_404(ForumChannel, slug=slug)
    if not permissions.can_manage_members(request.user, channel):
        permissions.require_member(request, channel)
    if request.method == "POST":
        try:
            action = request.POST.get("action")
            if action == "remove":
                creation.remove_member(request.user, channel, request.POST.get("member"))
            elif action == "password":
                creation.change_password(request.user, channel, request.POST.get("password", ""))
                messages.success(request, _("Password changed."))
            else:
                raise creation.RoomRefused(_("Members join with the room password; "
                                             "nobody is added by name."))
        except creation.RoomRefused as exc:
            messages.error(request, str(exc))
        return redirect("forum:room_members", slug=channel.slug)
    writers = _writers(channel)
    active_ids = set(channel.forum_members.filter(is_active=True)
                     .values_list("person__user_id", flat=True))
    wrote = {w["user_id"] for w in writers}
    context = {"channel": channel, "active_tab": "members", "writers": writers,
               "silent_members": len(active_ids - wrote),
               "badges": channel.badges(),
               "needs_first_password": channel.access == "password" and not channel.password_verifier,
               "can_manage_members": permissions.can_manage_members(request.user, channel)}
    return render(request, "forum/room_members.html", PageProcessor().decorate(context, request))


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
        context["searchable_channels"] = permissions.readable_channels(
            self.request.user).filter(is_encrypted=False)
        from .search import encrypted_rooms_skipped

        context["encrypted_rooms_skipped"] = encrypted_rooms_skipped(self.request.user)
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
    }
    return PageProcessor().decorate(context, request)


def room_files(request, slug):
    """The room's files: everything posted in its chat, and nothing else.

    There is no upload control on this page, deliberately. A file enters a room
    by being posted in it — the chat's upload door already carries the size cap,
    the MIME allow-list and the membership check, and a second door onto the
    same room would be a second set of rules to keep in step.

    The room's old vault library is untouched and still holds whatever was
    uploaded to it; it is simply not what this tab shows any more. Those files
    remain reachable through Storage, and the whitelist that scopes them to
    this room's members is still synced on every membership change.
    """
    from django.core.paginator import Paginator
    from django.shortcuts import render

    from . import attachments

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)

    search = (request.GET.get("q") or "").strip()
    kind = (request.GET.get("kind") or "").strip()
    if kind not in (attachments.KIND_IMAGE, attachments.KIND_VOICE,
                    attachments.KIND_FILE):
        kind = ""

    rows = attachments.room_attachments(channel, search=search, kind=kind)
    summary = attachments.summarise(rows)
    page = Paginator(rows, 40).get_page(request.GET.get("page"))

    files = [{
        "message": message,
        "kind": attachments.kind_of(message),
        # The membership-checked door, the only one these bytes have. It
        # re-applies can_read and 404s a deleted message's file, so a link
        # that outlives the reader's membership stops working on its own.
        "url": reverse("forum:api_message_attachment", args=[message.id]),
        # Not a URL of its own: the chat has no per-message route, its history
        # arrives over a websocket and pages backwards through a keyset
        # cursor. The fragment is what the chat page reads to walk back
        # through history until it finds this message.
        "message_url": (reverse("forum:channel_detail", args=[channel.slug])
                        + f"#msg-{message.id}"),
    } for message in page.object_list]

    context = _room_context(request, channel, "files")
    context.update({
        "files": files,
        "page": page,
        "search": search,
        "kind": kind,
        "file_count": summary["count"],
        "total_bytes": summary["bytes"],
    })
    return render(request, "forum/room_files.html", context)


def room_polls(request, slug):
    """The room's polls: its own data, its own rules, its own page."""
    from django.shortcuts import render

    from . import voting

    if not request.user.is_authenticated:
        from django.contrib.auth.views import redirect_to_login

        return redirect_to_login(request.get_full_path())
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)

    cards = []
    for poll, counted, ballot in voting.page_of(channel, request.user):
        visible = poll.results_visible
        cards.append({
            "poll": poll,
            "is_open": poll.is_open,
            # A withheld count is withheld from the CONTEXT, not merely from
            # the markup: a template guard is one `{% if %}` away from being
            # forgotten by the next person to touch this page, and the numbers
            # would still have been sitting in the response to find.
            "tally": counted if visible else None,
            "results_visible": visible,
            "rows": [{"result": r,
                      "label": r.label,
                      "ballots": r.ballots if visible else None,
                      "share_percent": counted.share(r) if visible else None}
                     for r in counted.results],
            "ballot": ballot,
            "can_manage": voting.may_manage(poll, request.user),
        })

    context = _room_context(request, channel, "polls")
    context["cards"] = cards
    return render(request, "forum/room_polls.html", context)


def room_poll_create(request, slug):
    """POST from the Create Poll modal. Any active member."""
    from django.core.exceptions import ValidationError
    from django.utils import timezone as tz
    from django.utils.dateparse import parse_datetime

    from . import voting
    from .models import ResultVisibility, Revisability

    if request.method != "POST":
        return redirect("forum:room_polls", slug=slug)
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)

    closes_raw = (request.POST.get("closes_at") or "").strip()
    closes_at = parse_datetime(closes_raw) if closes_raw else None
    if closes_at is not None and tz.is_naive(closes_at):
        closes_at = tz.make_aware(closes_at)

    # Both knobs are opt-in and both default to the friendlier answer: you may
    # change your mind, and everyone watches the count.
    revisability = (Revisability.FINAL
                    if request.POST.get("final") else Revisability.OPEN)
    visibility = (ResultVisibility.ON_CLOSE
                  if request.POST.get("hide_results") else ResultVisibility.LIVE)

    try:
        voting.open_poll(channel, request.user,
                         title=request.POST.get("title", ""),
                         options=request.POST.get("options", ""),
                         closes_at=closes_at, revisability=revisability,
                         visibility=visibility)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, _("The poll is open."))
    return redirect("forum:room_polls", slug=slug)


def room_poll_vote(request, slug, poll_slug):
    """POST one answer. Membership is checked twice on purpose: at the door
    by require_member, and inside cast() by the same predicate — the door
    could be reached another way one day, and the engine must not depend on
    who called it."""
    from . import voting
    from .models import PollChoice

    if request.method != "POST":
        return redirect("forum:room_polls", slug=slug)
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)
    poll = get_object_or_404(voting.polls_for(channel), slug=poll_slug)
    choice = get_object_or_404(PollChoice, pk=request.POST.get("choice") or 0,
                               poll=poll)

    try:
        voting.cast(poll, request.user, choice)
    except voting.VotingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _("Your answer has been recorded."))
    return redirect("forum:room_polls", slug=slug)


def room_poll_close(request, slug, poll_slug):
    """Shut a poll by hand — its author, or staff.

    The room had no way to do this before: a poll opened without a deadline
    stayed open forever, because the only close button lived in the separate
    polls app that no longer exists.
    """
    from . import voting

    if request.method != "POST":
        return redirect("forum:room_polls", slug=slug)
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)
    poll = get_object_or_404(voting.polls_for(channel), slug=poll_slug)
    if not voting.may_manage(poll, request.user):
        raise PermissionDenied(_("Only the person who opened this poll, or "
                                 "staff, may close it."))
    poll.close()
    messages.success(request, _("The poll is closed."))
    return redirect("forum:room_polls", slug=slug)


def room_poll_delete(request, slug, poll_slug):
    """Remove a poll and every answer to it. Its author, or staff."""
    from . import voting

    if request.method != "POST":
        return redirect("forum:room_polls", slug=slug)
    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_member(request, channel)
    poll = get_object_or_404(voting.polls_for(channel), slug=poll_slug)
    if not voting.may_manage(poll, request.user):
        raise PermissionDenied(_("Only the person who opened this poll, or "
                                 "staff, may delete it."))
    # Cascades to its choices and ballots. Irreversible, and the template asks
    # before it posts here.
    poll.delete()
    messages.success(request, _("The poll and its answers are gone."))
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


# ---------------------------------------------------------------------------
# Room settings — the fourth tab, and the whole of forum hygiene.
#
# There were forum-LEVEL Cleanup and Export desks until 2026-08-29, and they
# are gone rather than kept beside this: a retention period is a property of a
# conversation, not of a server, and an archive somebody can hand to the people
# in a room must not contain every other room. Once each room could do both for
# itself, the wide desks offered only a blunter version of the same two
# operations plus one dial nobody had asked to keep.
#
# WHAT WENT WITH THEM, so nobody looks for it: the whole-forum ZIP (archive
# room by room instead) and the UI for the PLATFORM DEFAULT retention period.
# `ForumRetentionPolicy.default()` still exists and still governs every room
# that has not set its own — rooms resolve through it — but it is now editable
# only in the Django admin. Its shipped value is 30 days with `enabled` FALSE,
# so the practical effect is that nothing expires anywhere until a room turns
# it on for itself, which is the safe direction.
#
# STAFF ONLY, checked in every one of these views and not merely hidden in the
# tab strip. `require_operator` answers 403 rather than 404 for the reason it
# documents: the URL is derived from a slug the member already knows.
# ---------------------------------------------------------------------------


def _room_hygiene_context(request, channel):
    """Everything the Settings tab shows, in one place.

    Shared by the GET and by every POST that falls back to re-rendering, so a
    form with errors cannot come back beside numbers computed differently.
    """
    from toto.celery_utils import celery_available

    from . import cleanup as cleanup_engine
    from .forms import ConfirmCleanupForm, RetentionSettingsForm
    from .models import ForumCleanupRun, ForumRetentionPolicy

    governing = ForumRetentionPolicy.current(channel)
    own = ForumRetentionPolicy.objects.filter(channel=channel).first()

    context = _room_context(request, channel, "settings")
    context.update({
        # `policy` is what GOVERNS the room, which may be the platform
        # default; `own_policy` is None until this room overrides it. The
        # template needs both to say "following the platform" honestly.
        "policy": governing,
        "own_policy": own,
        "follows_default": own is None,
        "settings_form": RetentionSettingsForm(instance=own or governing),
        "confirm_form": ConfirmCleanupForm(),
        "boundary": governing.boundary(),
        "preview": cleanup_engine.preview(governing, channel=channel),
        "last_run": ForumCleanupRun.objects.filter(channel=channel).first(),
        "recent_runs": ForumCleanupRun.objects.filter(channel=channel)[:10],
        "next_run": cleanup_engine.next_scheduled_run(),
        "worker_available": celery_available(),
        "in_flight": cleanup_engine.in_flight(channel),
        "page_title": f"{channel.name} — settings",
    })
    return context


@login_required
@require_safe
def room_archive(request, slug):
    """The Archive tab (2026-09-28, out of Settings): what a ZIP of this
    room would hold, and the button that streams it. Staff only, like the
    download it offers."""
    from django.shortcuts import render

    from . import export as export_engine
    from .models import ForumMessage

    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_operator(request)
    # Cheap aggregates only — the survey that walks every message and reads
    # every blob is the POST's job.
    files = (ForumMessage.objects.filter(channel=channel, deleted_at__isnull=True)
             .exclude(attachment="").exclude(attachment__isnull=True)
             .aggregate(n=models.Count("id"), total=models.Sum("attachment_size")))
    context = _room_context(request, channel, "archive")
    context.update({
        "message_count": ForumMessage.objects.filter(channel=channel).count(),
        "attachments": files["n"] or 0,
        "attachment_bytes": files["total"] or 0,
        "caps": {
            "messages_per_room": export_engine.MAX_MESSAGES_PER_ROOM,
            "attachments": export_engine.MAX_ATTACHMENTS,
            "total_mb": export_engine.MAX_TOTAL_BYTES // (1024 * 1024),
        },
        "page_title": f"{channel.name} — archive",
    })
    return render(request, "forum/room_archive.html", context)


@login_required
@require_safe
def room_settings(request, slug):
    """This room's retention period, its cleanup history, and its archive."""
    from django.shortcuts import render

    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_operator(request)
    return render(request, "forum/room_settings.html",
                  _room_hygiene_context(request, channel))


@login_required
@require_POST
def room_retention(request, slug):
    """Set this room's own retention period.

    Saving here is what makes the room STOP following the platform default:
    `for_channel` mints the override row from the default's value, so a staff
    member who opens the form and presses Save without changing anything gets
    the same number they were already on — pinned, and no longer moving when
    the platform dial does.
    """
    from .forms import RetentionSettingsForm
    from .models import ForumRetentionPolicy

    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_operator(request)

    # Validate FIRST, against no instance, and only then touch the database.
    # `for_channel()` CREATES the override row, and creating it is what makes
    # the room stop following the platform — run_scheduled excludes every
    # override channel from the wide sweep, disabled ones included. Minting it
    # before is_valid() meant a rejected save (an empty retention_days) still
    # detached the room, silently and permanently, with the page reporting
    # only that the form was bad.
    form = RetentionSettingsForm(request.POST)
    if form.is_valid():
        policy = ForumRetentionPolicy.for_channel(channel)
        policy.enabled = form.cleaned_data["enabled"]
        policy.retention_days = form.cleaned_data["retention_days"]
        # The form has no `channel` field and the row is minted from the slug
        # in the URL, so a forged POST cannot move an override across rooms.
        policy.updated_by = request.user
        policy.save()
        messages.success(request, _("Retention settings saved for this room."))
    else:
        messages.error(request, "; ".join(
            m for errors in form.errors.values() for m in errors))
    return redirect("forum:room_settings", slug=channel.slug)


@login_required
@require_POST
def room_retention_reset(request, slug):
    """Give up the override and follow the platform default again.

    Deletes the row rather than disabling it, because those two are different
    states and the difference is visible: a row with `enabled=False` is a room
    that has decided to keep everything, and `run_scheduled` excludes it from
    the forum-wide sweep for that reason. No row at all is a room that has not
    decided, and the platform dial governs it.
    """
    from .models import ForumRetentionPolicy

    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_operator(request)

    deleted, _ignored = (ForumRetentionPolicy.objects
                         .filter(channel=channel).delete())
    if deleted:
        messages.success(request, _("This room follows the platform "
                                    "retention setting again."))
    return redirect("forum:room_settings", slug=channel.slug)


@login_required
@require_POST
def room_cleanup_run(request, slug):
    """Run this room's cleanup now, after an explicit confirmation.

    The boundary is re-derived from the governing policy and the clock, never
    read from the page — the forum-wide endpoint carries the same rule and the
    same reason: a cutoff in a form field is a cutoff somebody can edit.
    """
    from toto.celery_utils import celery_available

    from . import cleanup as cleanup_engine
    from .forms import ConfirmCleanupForm
    from .models import TriggeredBy

    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_operator(request)

    form = ConfirmCleanupForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Nothing was deleted — the confirmation "
                                  "did not match."))
        return redirect("forum:room_settings", slug=channel.slug)

    try:
        run = cleanup_engine.trigger(triggered_by=TriggeredBy.MANUAL,
                                     user=request.user, channel=channel)
    except cleanup_engine.CleanupInProgress as exc:
        messages.error(request, str(exc))
        return redirect("forum:room_settings", slug=channel.slug)

    if celery_available():
        from .tasks import forum_cleanup_run

        forum_cleanup_run.delay(run.pk)
        messages.success(request, _("Cleanup started. This page shows the "
                                    "result when it finishes."))
        return redirect("forum:room_settings", slug=channel.slug)

    cleanup_engine.run_cleanup(run, deadline_seconds=25)
    run.refresh_from_db()
    if run.status == "partial":
        messages.warning(request, _(
            "Removed %(n)s message(s) before running out of time. Press Run "
            "cleanup now again, or start a worker.") % {
                "n": run.messages_deleted})
    else:
        messages.success(request, _(
            "Removed %(n)s message(s) and %(f)s file(s), permanently.") % {
                "n": run.messages_deleted, "f": run.attachments_deleted})
    return redirect("forum:room_settings", slug=channel.slug)


@login_required
@require_POST
def room_export_download(request, slug):
    """Archive THIS ROOM and stream it.

    `survey(channel=...)` is what keeps the archive to one room — the plan it
    returns holds one `RoomPlan`, so the index, the manifest and the media
    folder all name this room and nothing else. Scoping anywhere later would
    still have measured, and listed, rooms the reader may not see.
    """
    from asgiref.sync import sync_to_async
    from django.http import StreamingHttpResponse

    from . import export as export_engine

    channel = get_object_or_404(ForumChannel, slug=slug)
    permissions.require_operator(request)

    try:
        plan = export_engine.survey(actor=request.user.get_username(),
                                    channel=channel)
    except export_engine.ExportTooLarge as exc:
        messages.error(request, str(exc))
        return redirect("forum:room_archive", slug=channel.slug)

    chunks = export_engine.stream_archive(plan)

    async def astream():
        while True:
            chunk = await sync_to_async(next, thread_sensitive=True)(
                chunks, None)
            if chunk is None:
                return
            yield chunk

    response = StreamingHttpResponse(astream(), content_type="application/zip")
    response["Content-Disposition"] = (
        'attachment; filename="'
        f'{export_engine.export_filename(channel=channel)}"')
    return response
