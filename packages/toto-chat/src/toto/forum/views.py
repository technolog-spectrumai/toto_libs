"""The forum's doors: two pages and the JSON doors under them.

Every view is made by ``door(mark, …)`` and carries the mark as
``forum_door``; a test walks the URLconf and fails on a route without one.

    member      signed in, entitled, and may read the community's channel
                (a member, a senior member, the head; or an administrator)
    author      member, and the row's author or who moderates the channel
                (the view asks ``access`` about the row)
    moderator   member, and the community's head or an administrator

What a door answers before its own work, in this order: 405 for another
method; signed out, the sign-in page for a page and 403 for a JSON door;
403 for a write another site sent (Fetch Metadata); 402 for a plan without
the forum; 404 for a slug that names no community; 403 for a community the
member does not belong to; 503 when the forum's key cannot be opened. The
channel is made on the first opening (``channels.ensure_channel``).

Names, messages and poll texts leave as JSON strings or in a ``json_script``
block and are put on the page as text nodes, never as markup.
"""

from __future__ import annotations

import json
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.utils.translation import gettext as _

from . import access, channels, images, keys, posting, sealing, voting
from .posting import Refusal

MEMBER, AUTHOR, MODERATOR = "member", "author", "moderator"
MARKS = frozenset({MEMBER, AUTHOR, MODERATOR})

#: The most bytes of a JSON body.
MAX_BODY = 64 * 1024


def _json(payload, status=200, retry_after=None):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    if retry_after:
        response["Retry-After"] = str(int(retry_after))
    return response


def _refuse(request, page, message, status, retry_after=None):
    if page:
        response = render(request, "forum/refused.html",
                          {"sentence": str(message), "status": status}, status=status)
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


@door(MEMBER, method="GET", page=True)
def channel_list(request):
    """The member's communities, each with the way into its channel."""
    communities = list(access.communities_of(request.user))
    return render(request, "forum/channel_list.html", {"communities": communities})


@door(MEMBER, method="GET", page=True)
def channel_detail(request, channel):
    """The channel's page. Its data travels in a ``json_script`` block."""
    from .models import ForumSettings

    community = channel.community
    slug = community.slug
    nil = "00000000-0000-0000-0000-000000000000"
    config = {
        "community": {"name": community.name, "slug": slug},
        "viewer": {"name": posting.display_name(request.user),
                   "may_moderate": access.may_moderate(request.user, community)},
        "urls": {
            "feed": reverse("forum:feed", args=[slug]),
            "post": reverse("forum:post", args=[slug]),
            "poll_open": reverse("forum:poll_open", args=[slug]),
            # Patterns: the page puts a row's id where the nil id is.
            "message_remove": reverse("forum:message_remove", args=[slug, nil]),
            "poll_vote": reverse("forum:poll_vote", args=[slug, nil]),
            "poll_close": reverse("forum:poll_close", args=[slug, nil]),
            "poll_remove": reverse("forum:poll_remove", args=[slug, nil]),
            "nil": nil,
        },
        "limits": {"text_bytes": posting.MAX_TEXT_BYTES, "image_bytes": images.MAX_BYTES,
                   "image_types": sorted(images.TYPES), "poll_options": voting.MAX_OPTIONS},
        "refresh_seconds": ForumSettings.current().refresh_seconds,
        "feed": posting.feed(request.user, channel),
    }
    response = render(request, "forum/channel.html", {
        "community": community, "channel": channel, "forum_config": config,
        "communities": list(access.communities_of(request.user)),
    })
    response["Cache-Control"] = "no-store"
    return response


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


@door(MEMBER, method="GET")
def feed(request, channel):
    return posting.feed(request.user, channel, after=request.GET.get("after"),
                        before=request.GET.get("before"), limit=request.GET.get("limit"))


@door(MEMBER)
def post(request, channel):
    """Post text, an image, or both. Form fields ``op`` and ``text``, a file
    ``image``. 201 with the message; 200 with ``"replay": true`` for a
    repeated op."""
    upload = request.FILES.get("image")
    image = images.read_upload(upload) if upload is not None else None
    message, replay = posting.post_message(
        request.user, channel, text=request.POST.get("text", ""),
        op=request.POST.get("op"), image=image)
    payload = posting.message_to_dict(
        message, key=_key(channel), user=request.user, slug=channel.community.slug,
        moderator=access.may_moderate(request.user, channel.community))
    return {"message": payload, "replay": replay}, (200 if replay else 201)


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
        moderator=access.may_moderate(request.user, channel.community))[0]


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
            options=data.get("options"), closes_at=_closes_at(data.get("closes_at")),
            revisability=data.get("revisability"), visibility=data.get("visibility"))
    except ValidationError as exc:
        raise Refusal(" ".join(exc.messages), 400) from None
    return {"poll": _poll_answer(request, channel, poll)}, 201


@door(MEMBER)
def poll_vote(request, channel, poll_id):
    from .models import PollChoice

    poll = _poll(channel, poll_id)
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


@door(AUTHOR)
def poll_close(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    if not access.may_manage_poll(request.user, poll):
        raise Refusal(_("Only who opened a poll or the community's head closes it."), 403)
    voting.close_poll(poll)
    return {"poll": _poll_answer(request, channel, poll)}


@door(AUTHOR)
def poll_remove(request, channel, poll_id):
    poll = _poll(channel, poll_id)
    if not access.may_manage_poll(request.user, poll):
        raise Refusal(_("Only who opened a poll or the community's head removes it."), 403)
    voting.remove_poll(poll, request.user)
    return {"poll": {"id": str(poll.id), "number": poll.number, "seq": poll.seq,
                     "removed": True}}
