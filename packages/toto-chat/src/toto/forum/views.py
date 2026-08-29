from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
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
        # Staff-only forum controls. Hidden rather than disabled: a link that
        # always answers 403 is worse than no link — and the page behind it
        # re-checks, because hiding is cosmetic.
        context["is_operator"] = permissions.is_operator(self.request.user)
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

        context["can_send_messages"] = current_member is not None
        context["can_join"] = bool(current_person and not current_member)
        context["can_leave"] = current_member is not None

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
        channel = get_object_or_404(ForumChannel, slug=slug)

        person = Person.objects.filter(user=request.user).first()
        if not person:
            messages.error(request, _("Your user is not linked to a person profile, so you can only observe this channel."))
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
            messages.error(request, _("A channel needs a name."))
            return redirect("forum:channel_list")

        slug = slugify(name)[:50]
        if not slug:
            messages.error(request, _("That name cannot be turned into a URL slug."))
            return redirect("forum:channel_list")

        if slug in ForumChannel.RESERVED_SLUGS:
            # The model refuses this too; here it gets a sentence rather than
            # a validation error, because this is the door people use.
            messages.error(request, _(
                "“%(name)s” is one of the forum's own addresses. "
                "A room with that name could never be opened.")
                % {"name": name})
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
# Cleanup — forum-level, staff only.
#
# Deliberately NOT a room tab: the retention period covers the whole forum, and
# a global dial edited from inside one room reads as if it applied to that room
# only. So `_room_tabs.html`, `channel_base.html`, `_room_context` and
# `ChannelDetailView.get_context_data` are all untouched here, and the
# two-context-producer trap does not apply.
# ---------------------------------------------------------------------------


@login_required
@require_safe
def cleanup_page(request):
    """What the retention period is, what it did, and what it would do next."""
    from django.shortcuts import render

    from toto.celery_utils import celery_available

    from . import cleanup as cleanup_engine
    from .forms import ConfirmCleanupForm, RetentionSettingsForm
    from .models import ForumCleanupRun, ForumRetentionPolicy

    permissions.require_operator(request)
    policy = ForumRetentionPolicy.current()

    context = {
        "policy": policy,
        "settings_form": RetentionSettingsForm(instance=policy),
        "confirm_form": ConfirmCleanupForm(),
        "boundary": policy.boundary(),
        "preview": cleanup_engine.preview(policy),
        "last_run": ForumCleanupRun.objects.first(),
        "recent_runs": ForumCleanupRun.objects.all()[:10],
        "next_run": cleanup_engine.next_scheduled_run(),
        # Asked once, and said out loud on the page: without a worker the
        # schedule never fires and "next run" is a time nothing will act on.
        "worker_available": celery_available(),
        "in_flight": cleanup_engine.in_flight(),
        "page_title": "Forum cleanup",
    }
    return render(request, "forum/cleanup.html",
                  PageProcessor().decorate(context, request))


@login_required
@require_POST
def cleanup_settings(request):
    """Save the dial. Staff only, re-checked here and not merely hidden."""
    from .forms import RetentionSettingsForm
    from .models import ForumRetentionPolicy

    permissions.require_operator(request)
    policy = ForumRetentionPolicy.current()
    form = RetentionSettingsForm(request.POST, instance=policy)
    if form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        messages.success(request, _("Retention settings saved."))
    else:
        messages.error(request, "; ".join(
            m for errors in form.errors.values() for m in errors))
    return redirect("forum:cleanup")


@login_required
@require_POST
def cleanup_run(request):
    """Run it now, after an explicit confirmation.

    The boundary is RE-DERIVED here from the policy and the clock. Nothing the
    preview put on the page is trusted: a form field carrying a cutoff would be
    a cutoff somebody could edit, and a stale one would delete more than the
    screen said it would.
    """
    from toto.celery_utils import celery_available

    from . import cleanup as cleanup_engine
    from .forms import ConfirmCleanupForm
    from .models import TriggeredBy

    permissions.require_operator(request)
    form = ConfirmCleanupForm(request.POST)
    if not form.is_valid():
        messages.error(request, _("Nothing was deleted — the confirmation "
                                  "did not match."))
        return redirect("forum:cleanup")

    try:
        run = cleanup_engine.trigger(triggered_by=TriggeredBy.MANUAL,
                                     user=request.user)
    except cleanup_engine.CleanupInProgress as exc:
        messages.error(request, str(exc))
        return redirect("forum:cleanup")

    if celery_available():
        from .tasks import forum_cleanup

        forum_cleanup.delay()
        messages.success(request, _("Cleanup started. This page shows the "
                                    "result when it finishes."))
        return redirect("forum:cleanup")

    # No worker: run it here, on a short leash, and say honestly how far it
    # got. Every chunk commits, so stopping part-way leaves nothing
    # inconsistent and pressing the button again resumes.
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
    return redirect("forum:cleanup")


# ---------------------------------------------------------------------------
# Export — staff only, every room, and honest about both.
# ---------------------------------------------------------------------------


@login_required
@require_safe
def forum_export(request):
    """The desk: how big the archive would be, and what it would contain.

    Aggregates only. This page must never walk every message — that is the
    POST's job, and a desk that surveyed the whole forum on each visit would
    read every blob on the disk to draw a number.
    """
    from django.shortcuts import render

    from . import export as export_engine
    from .models import ForumChannel, ForumMessage

    permissions.require_operator(request)

    # Trailing order_by(): ForumMessage.Meta.ordering folds into the GROUP BY
    # and would return one row per message instead of one per room.
    per_room = (ForumMessage.objects.values("channel_id")
                .annotate(n=models.Count("id")).order_by("channel_id"))
    files = (ForumMessage.objects.filter(deleted_at__isnull=True)
             .exclude(attachment="").exclude(attachment__isnull=True)
             .aggregate(n=models.Count("id"),
                        total=models.Sum("attachment_size")))

    context = {
        "rooms": ForumChannel.objects.count(),
        # NOT "messages": that key is django.contrib.messages in every
        # template, and shadowing it makes the base chrome try to iterate an
        # int. channel_details.html carries the same warning for the same
        # reason — it calls its own key `initial_messages`.
        "message_count": ForumMessage.objects.count(),
        "rooms_with_messages": len(list(per_room)),
        "attachments": files["n"] or 0,
        "attachment_bytes": files["total"] or 0,
        "caps": {
            "messages_per_room": export_engine.MAX_MESSAGES_PER_ROOM,
            "messages_total": export_engine.MAX_MESSAGES_TOTAL,
            "rooms": export_engine.MAX_ROOMS,
            "attachments": export_engine.MAX_ATTACHMENTS,
            "total_mb": export_engine.MAX_TOTAL_BYTES // (1024 * 1024),
        },
        "page_title": "Export the forum",
    }
    return render(request, "forum/export.html",
                  PageProcessor().decorate(context, request))


@login_required
@require_POST
def forum_export_download(request):
    """Build the archive and stream it.

    The survey runs first and can still refuse with a redirect and a sentence;
    once the first byte has gone the status is fixed at 200, which is exactly
    why the counting happens before anything is written.
    """
    from asgiref.sync import sync_to_async
    from django.http import StreamingHttpResponse

    from . import export as export_engine

    permissions.require_operator(request)
    try:
        plan = export_engine.survey(actor=request.user.get_username())
    except export_engine.ExportTooLarge as exc:
        messages.error(request, str(exc))
        return redirect("forum:export")

    chunks = export_engine.stream_archive(plan)

    async def astream():
        # Pulled through a worker thread so the ORM and the template renders
        # stay synchronous while ASGI streams for real. Django's own
        # __aiter__ would materialise the whole archive in memory first, which
        # is the thing this response exists to avoid.
        while True:
            chunk = await sync_to_async(next, thread_sensitive=True)(
                chunks, None)
            if chunk is None:
                return
            yield chunk

    response = StreamingHttpResponse(astream(), content_type="application/zip")
    response["Content-Disposition"] = (
        f'attachment; filename="{export_engine.export_filename()}"')
    return response


# ---------------------------------------------------------------------------
# Room settings — the fourth tab, and the room's own half of forum hygiene.
#
# The forum-level `/forum/cleanup/` and `/forum/export/` desks above are NOT
# replaced by this: they are the platform view, they set the default every room
# follows until it says otherwise, and they archive everything at once. This
# tab is the same two operations SCOPED TO ONE ROOM, which is what makes them
# usable — a retention period is a property of a conversation, not of a server,
# and an archive somebody can hand to the people in a room must not contain
# every other room.
#
# The comment above `cleanup_page` said the dial was "deliberately NOT a room
# tab, because a global dial edited from inside one room reads as if it applied
# to that room only". That reasoning was sound and its conclusion is now wrong:
# the dial edited here IS this room's, so the reading it warned against is
# simply the truth. The forum-wide dial stays where it was.
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
    from . import export as export_engine
    from .forms import ConfirmCleanupForm, RetentionSettingsForm
    from .models import ForumCleanupRun, ForumMessage, ForumRetentionPolicy

    governing = ForumRetentionPolicy.current(channel)
    own = ForumRetentionPolicy.objects.filter(channel=channel).first()

    files = (ForumMessage.objects.filter(channel=channel,
                                         deleted_at__isnull=True)
             .exclude(attachment="").exclude(attachment__isnull=True)
             .aggregate(n=models.Count("id"),
                        total=models.Sum("attachment_size")))

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
        # Archive counts. Cheap aggregates only — the survey that walks every
        # message and reads every blob is the POST's job, exactly as on the
        # forum-wide desk.
        "message_count": ForumMessage.objects.filter(channel=channel).count(),
        "attachments": files["n"] or 0,
        "attachment_bytes": files["total"] or 0,
        "caps": {
            "messages_per_room": export_engine.MAX_MESSAGES_PER_ROOM,
            "attachments": export_engine.MAX_ATTACHMENTS,
            "total_mb": export_engine.MAX_TOTAL_BYTES // (1024 * 1024),
        },
        "page_title": f"{channel.name} — settings",
    })
    return context


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

    policy = ForumRetentionPolicy.for_channel(channel)
    form = RetentionSettingsForm(request.POST, instance=policy)
    if form.is_valid():
        saved = form.save(commit=False)
        # Belt and braces: the form has no `channel` field, so this cannot be
        # posted from another room's page to move an override across rooms.
        saved.channel = channel
        saved.updated_by = request.user
        saved.save()
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
        return redirect("forum:room_settings", slug=channel.slug)

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
