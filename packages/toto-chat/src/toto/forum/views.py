"""The forum's doors: two pages and the JSON doors under them.

Every view is made by ``door(mark, …)`` and carries the mark as
``forum_door``; a test walks the URLconf and fails on a route without one.

    member          signed in, entitled, and may read the community's
                    channel (a member, a senior member, the head; or an
                    administrator)
    author          member, and the row's author or who moderates the
                    channel (the view asks ``access`` about the row)
    moderator       member, and the community's head or an administrator
    administrator   signed in, entitled, and an administrator of the
                    platform: a real superuser on the Superuser plan, never
                    staff alone, and not a community's head. The Settings
                    page and its two doors, which name no community

What a door answers before its own work, in this order: 405 for another
method; signed out, the sign-in page for a page and 403 for a JSON door;
403 for a write another site sent (Fetch Metadata); 402 for a plan without
the forum; 403 for who is no administrator, at an administrator's door; 404
for a slug that names no community; 403 for a community the member does not
belong to; 503 when the forum's key cannot be opened. The channel is made on
the first opening (``channels.ensure_channel``).

A post that cannot be charged is refused by the door too, in the ledger's
own words: 402 when the pool is short (or a fee is in arrears), 429 at the
day's cap (``billing.afford_post``).

Names, messages and poll texts leave as JSON strings or in a ``json_script``
block and are put on the page as text nodes, never as markup.

A page is handed the platform's own context (``PageProcessor``: the theme,
the font, the brand, the app bar's links), as every other app's pages are,
so it is drawn in the platform's frame.
"""

from __future__ import annotations

import json
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

from toto.quota.api import InArrears, QuotaExceeded
from toto.quota.charge import InsufficientFunds
from toto.ui import PageProcessor

from . import access, billing, channels, cleanup, images, keys, poll_audit, posting, sealing, threads, voting
from .models import ForumPollAudit, ForumSettings
from .posting import Refusal

MEMBER, AUTHOR, MODERATOR, ADMINISTRATOR = "member", "author", "moderator", "administrator"
MARKS = frozenset({MEMBER, AUTHOR, MODERATOR, ADMINISTRATOR})

#: The ledger's own refusals of a charge: each has ``status_code`` (402, or
#: 429 for a cap) and reads as a sentence.
UNPAID = (QuotaExceeded, InArrears, InsufficientFunds)

#: The most bytes of a JSON body.
MAX_BODY = 64 * 1024


def _json(payload, status=200, retry_after=None):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    if retry_after:
        response["Retry-After"] = str(int(retry_after))
    return response


def _page(request, context):
    """``context`` with what every page of the platform is handed: the
    theme, the font, the brand and the app bar's links."""
    return PageProcessor().decorate(context, request)


def _refuse(request, page, message, status, retry_after=None):
    if page:
        context = {"sentence": str(message), "status": status}
        # Never at the cost of the refusal: with no active Platform the
        # page loses its styling and keeps its status.
        try:
            context = _page(request, context)
        except Http404:
            pass
        response = render(request, "forum/refused.html", context, status=status)
        response["Cache-Control"] = "no-store"
        return response
    return _json({"error": str(message)}, status, retry_after)


def _community(slug):
    from toto.socialhub.models import Community

    return Community.objects.filter(slug=slug).first()


def door(mark, *, method="POST", page=False):
    """Make a door: the checks of the module docstring, then the view with
    ``(request, channel, …)`` (``(request)`` for the list, which names no
    community). A page view answers a response; a JSON door a dict, or
    ``(dict, status)``."""
    assert mark in MARKS

    def decorate(view):
        @wraps(view)
        def wrapped(request, slug=None, **kwargs):
            if request.method != method:
                response = _refuse(request, page, _("This address takes %(method)s only.")
                                   % {"method": method}, 405)
                response["Allow"] = method
                return response
            user = request.user
            if not access.signed_in(user):
                if page:
                    return redirect_to_login(request.get_full_path())
                return _refuse(request, page, _("Sign in to use the forum."), 403)
            if method != "GET":
                from toto.api.fetch_metadata import cross_site_refusal

                refused = cross_site_refusal(request)
                if refused is not None:
                    return refused
            if not access.entitled(user):
                return _refuse(request, page,
                               _("The forum is not part of your plan."), 402)
            if mark == ADMINISTRATOR and not access.is_administrator(user):
                return _refuse(request, page,
                               _("Only an administrator of the platform opens the "
                                 "forum's settings."), 403)
            try:
                if slug is None:
                    return view(request, **kwargs)
                community = _community(slug)
                if community is None:
                    return _refuse(request, page, _("Community not found."), 404)
                if not access.may_read(user, community):
                    return _refuse(request, page,
                                   _("Only members of this community use its channel."), 403)
                if mark == MODERATOR and not access.may_moderate(user, community):
                    return _refuse(request, page,
                                   _("Only the community's head moderates its channel."), 403)
                try:
                    channel = channels.ensure_channel(community)
                except keys.ChannelKeyUnavailable:
                    return _refuse(request, page, _(
                        "The forum's key is not available on this server, so nothing can be "
                        "read or stored. Tell an administrator."), 503)
                answer = view(request, channel, **kwargs)
            except Refusal as exc:
                return _refuse(request, page, exc, exc.status_code, exc.retry_after)
            except images.ImageRefused as exc:
                return _refuse(request, page, exc, exc.status_code)
            except UNPAID as exc:
                return _refuse(request, page, exc, exc.status_code)
            if isinstance(answer, HttpResponse):
                return answer
            if isinstance(answer, tuple):
                return _json(*answer)
            return _json(answer)

        wrapped.forum_door = mark
        return wrapped

    return decorate


def _body(request) -> dict:
    """The JSON object a door was sent, or 400."""
    if len(request.body) > MAX_BODY:
        raise Refusal(_("That request is too large."), 400)
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError, RecursionError):
        data = None
    if not isinstance(data, dict):
        raise Refusal(_("Send a JSON object."), 400)
    return data


def _message(channel, message_id):
    from .models import ForumMessage

    row = ForumMessage.objects.filter(pk=message_id, channel=channel).select_related(
        "channel__community").first()
    if row is None:
        raise Refusal(_("Message not found."), 404)
    return row


def _poll(channel, poll_id):
    from .models import ChannelPoll

    row = ChannelPoll.objects.filter(pk=poll_id, channel=channel).select_related(
        "channel__community").first()
    if row is None or row.removed_at is not None:
        raise Refusal(_("Poll not found."), 404)
    return row


def _key(channel) -> bytes:
    return posting._key(channel)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------


#: A channel page's tabs, in their order (the owner, 2026-10-07: "polls and
#: settings have to be TABS", "the 3rd tab is search", "4th tab is images").
#: Settings, an administrator's, is a page of its own and the last tab.
CHANNEL_TABS = (
    ("messages", gettext_lazy("Messages"), "fa-solid fa-comments"),
    ("polls", gettext_lazy("Polls"), "fa-solid fa-square-poll-vertical"),
    ("search", gettext_lazy("Search"), "fa-solid fa-magnifying-glass"),
    ("images", gettext_lazy("Images"), "fa-solid fa-images"),
)


def _tab(request) -> str:
    """The tab the address asks for (``?tab=polls``), so that a reload and a
    link keep it; Messages for anything else."""
    asked = request.GET.get("tab", "")
    return asked if asked in {key for key, _label, _icon in CHANNEL_TABS} else "messages"


def _tabs(user, *, community=None, active="", inpage=False) -> list:
    """The forum's tab strip (``forum/_tabs.html``). With a community: its
    channel's four tabs (on the channel's own page they change the panel in
    place, ``inpage``; elsewhere they are links into it). Without one: the
    list of channels. Then Settings, for an administrator only."""
    tabs = []
    if community is not None:
        base = reverse("forum:channel_detail", args=[community.slug])
        for key, label, icon in CHANNEL_TABS:
            tabs.append({"key": key, "label": label, "icon": icon, "inpage": inpage,
                         "href": base if key == "messages" else f"{base}?tab={key}",
                         "active": key == active})
    else:
        tabs.append({"key": "channels", "label": _("Channels"), "icon": "fa-solid fa-comments",
                     "inpage": False, "href": reverse("forum:channel_list"),
                     "active": active == "channels"})
    if access.is_administrator(user):
        href = reverse("forum:settings")
        if community is not None:
            href = f"{href}?from={community.slug}"
        tabs.append({"key": "settings", "label": _("Settings"), "icon": "fa-solid fa-sliders",
                     "inpage": False, "href": href, "active": active == "settings"})
    return tabs


def _settings_url(user):
    """The Settings page's address for an administrator, else None: what a
    page reads to draw the link, or not."""
    return reverse("forum:settings") if access.is_administrator(user) else None


SORT_MODES = (("new", gettext_lazy("New")), ("active", gettext_lazy("Active")),
              ("unanswered", gettext_lazy("Unanswered")),
              ("closed", gettext_lazy("Closed")), ("archived", gettext_lazy("Archived")))


@door(MEMBER, method="GET", page=True)
def channel_list(request):
    """A feed of poll threads in the member's communities."""
    communities = list(access.communities_of(request.user))
    listing = threads.page(request.user, communities, mode=request.GET.get("sort", "new"),
                           query=request.GET.get("q", ""), number=request.GET.get("page", 1))
    return render(request, "forum/feed.html", _page(request, {
        "communities": communities, "forum_settings_url": _settings_url(request.user),
        "scope": None, "sort_modes": SORT_MODES, **listing}))


@door(MEMBER, method="GET", page=True)
def channel_detail(request, channel):
    """A community's poll thread feed."""
    community = channel.community
    listing = threads.page(request.user, access.communities_of(request.user),
                           community=community, mode=request.GET.get("sort", "new"),
                           query=request.GET.get("q", ""), number=request.GET.get("page", 1))
    response = render(request, "forum/feed.html", _page(request, {
        "communities": list(access.communities_of(request.user)),
        "forum_settings_url": _settings_url(request.user), "scope": community,
        "sort_modes": SORT_MODES,
        **listing,
    }))
    response["Cache-Control"] = "no-store"
    return response


@door(MEMBER, method="GET", page=True)
def thread_detail(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    slug = channel.slug
    nil = "00000000-0000-0000-0000-000000000000"
    dials = ForumSettings.current()
    config = {
        "community": {"name": channel.name, "slug": slug},
        "viewer": {"name": posting.display_name(request.user),
                   "may_moderate": access.may_moderate(request.user, channel.community),
                   "is_admin": access.is_administrator(request.user)},
        "urls": {
            "feed": reverse("forum:thread_feed", args=[slug, poll.pk]),
            "post": reverse("forum:post", args=[slug]),
            "community": reverse("forum:channel_detail", args=[slug]),
            # Patterns: the page puts a row's id where the nil id is.
            "message_remove": reverse("forum:message_remove", args=[slug, nil]),
            "poll_vote": reverse("forum:poll_vote", args=[slug, poll.pk]),
            "poll_reset": reverse("forum:poll_reset", args=[slug, poll.pk]),
            "poll_close": reverse("forum:poll_close", args=[slug, poll.pk]),
            "poll_archive": reverse("forum:poll_archive", args=[slug, poll.pk]),
            "poll_remove": reverse("forum:poll_remove", args=[slug, poll.pk]),
            # What a post would cost, before it is sent (the door below).
            "estimate": reverse("forum:estimate", args=[slug]),
            "nil": nil,
        },
        "limits": {"text_bytes": posting.MAX_TEXT_BYTES, "image_bytes": images.MAX_BYTES,
                   "image_types": sorted(images.TYPES), "poll_options": voting.MAX_OPTIONS},
        # The list price of a kilobyte of text and of image, as decimal
        # strings ("0" where nothing is priced). The estimate door has the
        # member's own figure, their discount included.
        # ``free_below_kb``: a post smaller than this (text and picture
        # together) costs nothing; 0 where every post is charged.
        "prices": {**billing.prices(), "free_below_kb": dials.free_below_kb},
        "refresh_seconds": dials.refresh_seconds,
        "feed": posting.thread_feed(request.user, poll),
    }
    config["feed"]["poll"]["community_name"] = channel.name
    config["feed"]["poll"]["community_slug"] = slug
    response = render(request, "forum/thread.html", _page(request, {
        "community": channel.community, "channel": channel, "poll": config["feed"]["poll"],
        "replies": config["feed"]["messages"], "older_replies": config["feed"]["more"],
        "forum_is_admin": access.is_administrator(request.user),
        "forum_config": config,
        "communities": list(access.communities_of(request.user)),
        "forum_settings_url": _settings_url(request.user),
    }))
    response["Cache-Control"] = "no-store"
    return response


@door(MEMBER, method="GET")
def thread_feed(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    return posting.thread_feed(request.user, poll, after=request.GET.get("after"),
                               before=request.GET.get("before"), limit=request.GET.get("limit"))


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@door(MEMBER, method="GET")
def feed(request, channel):
    return posting.feed(request.user, channel, after=request.GET.get("after"),
                        before=request.GET.get("before"), limit=request.GET.get("limit"))


@door(MEMBER)
def post(request, channel):
    """Post one linear reply in a community thread."""
    poll_id = request.POST.get("poll")
    if not poll_id:
        raise Refusal(_("Choose a thread before posting."), 400)
    try:
        poll = _poll(channel, poll_id)
    except (ValidationError, ValueError):
        raise Refusal(_("Poll not found."), 404) from None
    upload = request.FILES.get("image")
    image = images.read_upload(upload) if upload is not None else None
    message, replay = posting.post_message(
        request.user, channel, poll=poll, text=request.POST.get("text", ""),
        op=request.POST.get("op"), image=image)
    payload = posting.message_to_dict(
        message, key=_key(channel), user=request.user, slug=channel.community.slug,
        moderator=access.may_moderate(request.user, channel.community))
    return {"message": payload, "replay": replay}, (200 if replay else 201)


def _size(data, name) -> int:
    """A size in bytes from a door's input: a whole number, 0 or more,
    absent meaning 0. Else 400."""
    value = data.get(name)
    if value in (None, ""):
        return 0
    refusal = Refusal(_("“%(name)s” must be a whole number.") % {"name": name}, 400)
    if isinstance(value, bool) or isinstance(value, float):
        raise refusal
    if isinstance(value, str):
        if not (value.isascii() and value.isdigit()) or len(value) > 12:
            raise refusal
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise refusal
    return value


@door(MEMBER)
def estimate(request, channel):
    """What a post would cost this member, before it is sent.

    ``text_bytes`` and ``image_bytes``, each a whole number of bytes (0 or
    absent for none), form-encoded or as a JSON object. Answers ``{"amount",
    "text", "image"}`` as decimal strings, ``"affordable"``, ``"balance"``
    (a decimal string, or null where nothing is priced), ``"display"``,
    the amount as the platform writes it with the pool's name ("" where
    nothing is priced or the post is free) and ``"free"``: the post is under
    the free threshold of the forum's Settings. Nothing is stored and nothing is charged: it is the
    charge's own arithmetic (``billing.quote``). A size no post may have is
    400, in the posting door's words."""
    if (request.content_type or "").split(";")[0].strip() == "application/json":
        data = _body(request)
    else:
        data = request.POST
    text_bytes = _size(data, "text_bytes")
    image_bytes = _size(data, "image_bytes")
    if text_bytes > posting.MAX_TEXT_BYTES:
        raise Refusal(_("The text is too long: %(size)d bytes, and the most is %(most)d.")
                      % {"size": text_bytes, "most": posting.MAX_TEXT_BYTES}, 400)
    if image_bytes > images.MAX_BYTES:
        raise Refusal(_("The image is too large. The most is %(size)s MB.")
                      % {"size": images.MAX_BYTES // (1024 * 1024)}, 400)
    return billing.quote(request.user, text_bytes=text_bytes,
                         image_bytes=image_bytes).as_dict()


@door(MEMBER, method="GET")
def search(request, channel):
    """The channel's messages that hold ``?q=``, the newest first:
    ``{"query", "messages", "scanned", "capped"}``. Free; nothing is kept."""
    return posting.search(request.user, channel, request.GET.get("q"))


@door(MEMBER, method="GET")
def image_list(request, channel):
    """The channel's pictures, the newest first, a page at a time
    (``?before=<number>``): ``{"messages", "more", "oldest"}``."""
    return posting.images_page(request.user, channel, before=request.GET.get("before"),
                               limit=request.GET.get("limit"))


@door(AUTHOR)
def message_remove(request, channel, message_id):
    message = _message(channel, message_id)
    if not access.may_remove_message(request.user, message):
        raise Refusal(_("Only its author or the community's head removes a message."), 403)
    posting.remove_message(request.user, message)
    return {"message": {"id": str(message.id), "number": message.number,
                        "seq": message.seq, "removed": True}}


@door(MEMBER, method="GET")
def message_image(request, channel, message_id):
    """A message's image, opened for who may read the channel. Served with
    the type its own bytes had when it was stored, never guessed."""
    message = _message(channel, message_id)
    if message.removed_at is not None or message.attachment_id is None:
        raise Refusal(_("This message has no image."), 404)
    if message.attachment_mime not in images.TYPES:
        raise Refusal(_("This message has no image."), 404)
    try:
        data = images.open_image(message, _key(channel))
    except FileNotFoundError:
        raise Refusal(_("This message has no image."), 404) from None
    except sealing.SealBroken:
        raise Refusal(_("This image cannot be opened."), 409) from None
    if images.sniff(data) != message.attachment_mime:
        raise Refusal(_("This image cannot be opened."), 409)
    response = HttpResponse(data, content_type=message.attachment_mime)
    response["Content-Disposition"] = "inline"
    response["X-Content-Type-Options"] = "nosniff"
    response["Cache-Control"] = "private, no-store"
    return response


# ---------------------------------------------------------------------------
# Polls
# ---------------------------------------------------------------------------


def _poll_answer(request, channel, poll):
    poll.refresh_from_db()
    return voting.polls_to_dicts(
        [poll], key=_key(channel), user=request.user,
        moderator=access.is_administrator(request.user))[0]


def _closes_at(value):
    if value in (None, ""):
        return None
    moment = parse_datetime(value) if isinstance(value, str) else None
    if moment is None:
        raise Refusal(_("The closing time is not a date and time."), 400)
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment)
    return moment


@door(MEMBER)
def poll_open(request, channel):
    data = _body(request)
    try:
        poll = voting.open_poll(
            channel, request.user, _key(channel), title=data.get("title"),
            description=data.get("description"), options=data.get("options"),
            closes_at=_closes_at(data.get("closes_at")),
            visibility=data.get("visibility"))
    except ValidationError as exc:
        raise Refusal(" ".join(exc.messages), 400) from None
    return {"poll": _poll_answer(request, channel, poll),
            "thread_url": reverse("forum:thread_detail", args=[channel.slug, poll.pk])}, 201


@door(MEMBER)
def poll_vote(request, channel, poll_id):
    from .models import PollChoice

    poll = _poll(channel, poll_id)
    voting.close_due(poll)
    data = _body(request)
    raw = data.get("choice")
    choice = None
    if isinstance(raw, int) and not isinstance(raw, bool):
        choice = PollChoice.objects.filter(pk=raw, poll=poll).first()
    try:
        voting.cast(poll, request.user, choice)
    except voting.NotOpen as exc:
        raise Refusal(exc, 409) from None
    except voting.AlreadyAnswered as exc:
        raise Refusal(exc, 409) from None
    except voting.UnknownChoice as exc:
        raise Refusal(exc, 400) from None
    except voting.NotEligible as exc:
        raise Refusal(exc, 403) from None
    return {"poll": _poll_answer(request, channel, poll)}


@door(MEMBER)
def poll_reset(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    voting.close_due(poll)
    try:
        voting.reset_vote(poll, request.user)
    except voting.NotOpen as exc:
        raise Refusal(exc, 409) from None
    return {"poll": _poll_answer(request, channel, poll)}


@door(AUTHOR)
def poll_close(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    if not access.may_manage_poll(request.user, poll):
        raise Refusal(_("Only the poll author or an administrator closes it."), 403)
    voting.close_poll(poll, request.user)
    return {"poll": _poll_answer(request, channel, poll)}


@door(ADMINISTRATOR)
def poll_archive(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    try:
        voting.archive_poll(poll, request.user)
    except ValidationError as exc:
        raise Refusal(" ".join(exc.messages), 409) from None
    return {"poll": _poll_answer(request, channel, poll)}


@door(AUTHOR)
def poll_remove(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    if not access.may_manage_poll(request.user, poll):
        raise Refusal(_("Only the poll author or an administrator deletes it."), 403)
    voting.remove_poll(poll, request.user)
    return {"poll": {"id": str(poll.id), "number": poll.number, "seq": poll.seq,
                     "removed": True}}


# ---------------------------------------------------------------------------
# Settings: the retention age, the refresh interval, and cleanup by hand.
# For an administrator of the platform only (the mark); a community's head
# moderates one channel and has no dial here. Each door is a form: POST, then
# a redirect back to the page with a sentence.
# ---------------------------------------------------------------------------


class SettingsForm(forms.ModelForm):
    """The dials, checked by the model's own bounds: the age from 1 to 3650
    days, the interval from 2 to 120 seconds, the free threshold from 0 to
    102400 kilobytes."""

    class Meta:
        model = ForumSettings
        fields = ["retention_enabled", "retention_days", "refresh_seconds", "free_below_kb"]
        labels = {
            "retention_enabled": gettext_lazy("Remove old closed threads on a schedule"),
            "retention_days": gettext_lazy("Retention age, in days"),
            "refresh_seconds": gettext_lazy("Refresh interval, in seconds"),
            "free_below_kb": gettext_lazy("Messages are free below, in kilobytes"),
        }


@door(ADMINISTRATOR, method="GET", page=True)
def poll_audit_page(request):
    events = []
    rows = ForumPollAudit.objects.select_related("channel__community", "actor")[:100]
    for row in rows:
        try:
            snapshot = poll_audit.read(row)
        except keys.ChannelKeyUnavailable:
            raise Refusal(_("The forum's key is not available on this server."), 503) from None
        events.append({"row": row, "snapshot": snapshot})
    return render(request, "forum/poll_audit.html", _page(request, {"events": events}))


def _errors(form) -> str:
    """A form's refusals as one line: each field's name, then what is wrong."""
    parts = []
    for name, errors in form.errors.items():
        label = form.fields[name].label if name in form.fields else ""
        parts.append(f"{label}: {' '.join(errors)}" if label else " ".join(errors))
    return " ".join(parts)


@door(ADMINISTRATOR, method="GET", page=True)
def settings_page(request):
    """The forum's settings, what a cleanup would remove now, and what the
    last ones did."""
    from .models import ForumChannel, ForumCleanupRun

    current = ForumSettings.current()
    # Opened from a channel's tab strip (``?from=<slug>``): the strip keeps
    # that channel's tabs beside Settings. Any other value: the list's strip.
    origin = None
    asked = request.GET.get("from", "")
    if asked:
        from toto.socialhub.models import Community

        origin = Community.objects.filter(slug=asked).first()
    response = render(request, "forum/settings.html", _page(request, {
        "forum_tabs": _tabs(request.user, community=origin, active="settings"),
        "settings_row": current,
        "form": SettingsForm(instance=current),
        "preview": cleanup.preview(current.boundary()),
        "next_run": cleanup.next_scheduled_run(),
        "in_flight": cleanup.in_flight(),
        "channels": list(ForumChannel.objects.select_related("community")
                         .order_by("community__name")),
        "runs": list(ForumCleanupRun.objects.select_related("triggered_by_user")[:20]),
        "min_days": cleanup.MIN_DAYS, "max_days": cleanup.MAX_DAYS,
    }))
    response["Cache-Control"] = "no-store"
    return response


@door(ADMINISTRATOR, page=True)
def settings_save(request):
    """Save the dials. Nothing is saved unless every one is in range."""
    form = SettingsForm(request.POST, instance=ForumSettings.current())
    if form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        messages.success(request, _("The forum's settings were saved."))
    else:
        messages.error(request, _("Nothing was saved. %(errors)s") % {"errors": _errors(form)})
    return redirect("forum:settings")


def _days(value):
    """An age in whole days within the bounds, or 400."""
    try:
        if not isinstance(value, str) or not (value.isascii() and value.isdigit()):
            raise ValueError
        days = int(value[:6])
    except ValueError:
        days = 0
    if not cleanup.MIN_DAYS <= days <= cleanup.MAX_DAYS:
        raise Refusal(_("The age is a whole number of days, from %(least)d to %(most)d.")
                      % {"least": cleanup.MIN_DAYS, "most": cleanup.MAX_DAYS}, 400)
    return days


@door(ADMINISTRATOR, page=True)
def cleanup_start(request):
    """Start a cleanup by hand: of one channel or of all, of what is older
    than an age or of everything.

    ``scope`` is ``all`` or a community's slug; ``age`` is ``retention`` (the
    saved age), ``days`` with ``days``, or ``everything``; ``confirm`` must
    be ticked. The boundary is worked out HERE, from the clock, when the
    cleanup is claimed: no instant the page showed is trusted. The removing
    happens on the worker (``dispatch.py``); without one, nothing is claimed
    and the page says so."""
    from . import dispatch
    from .models import ForumChannel

    def back(sentence):
        messages.error(request, sentence)
        return redirect("forum:settings")

    if request.POST.get("confirm") != "yes":
        return back(_("Nothing was removed: tick the box to confirm."))
    scope = request.POST.get("scope", "")
    channel = None
    if scope != "all":
        channel = (ForumChannel.objects.select_related("community")
                   .filter(community__slug=scope).first() if scope else None)
        if channel is None:
            return back(_("Nothing was removed: choose a channel, or all of them."))
    age = request.POST.get("age", "")
    try:
        if age == "everything":
            days = None
        elif age == "retention":
            days = ForumSettings.current().retention_days
        elif age == "days":
            days = _days(request.POST.get("days", ""))
        else:
            return back(_("Nothing was removed: choose what to remove."))
    except Refusal as exc:
        return back(_("Nothing was removed. %(reason)s") % {"reason": exc})
    try:
        dispatch.start_manual(request.user, channel=channel, days=days)
    except (dispatch.CannotQueue, cleanup.CleanupInProgress) as exc:
        return back(str(exc))
    messages.success(request, _("The cleanup was started. This page shows what it removed "
                                "when it has finished."))
    return redirect("forum:settings")
