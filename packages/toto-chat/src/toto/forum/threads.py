"""Community-scoped poll feeds and encrypted poll-and-reply search."""

from __future__ import annotations

from django.core.paginator import Paginator
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from . import access, posting, sealing, voting
from .models import ChannelPoll, ForumMessage, PollBallot, PollStatus

PAGE_SIZE = 20
SEARCH_SCAN = 2000
SEARCH_MAX = 100


def _matches(user, channels, query):
    needle = query.casefold()
    key_by_channel = {}

    def key(channel):
        if channel.pk not in key_by_channel:
            key_by_channel[channel.pk] = posting._key(channel)
        return key_by_channel[channel.pk]

    found = set()
    polls = (ChannelPoll.objects.filter(channel__in=channels, removed_at__isnull=True)
             .select_related("channel").order_by("-created_at")[:SEARCH_SCAN])
    for poll in polls:
        text = f"{voting.open_title(key(poll.channel), poll)}\n{voting.open_description(key(poll.channel), poll)}"
        if needle in text.casefold():
            found.add(poll.pk)
    replies = (ForumMessage.objects.filter(channel__in=channels, removed_at__isnull=True,
                                           poll__removed_at__isnull=True)
               .select_related("channel").order_by("-created_at")[:SEARCH_SCAN])
    for reply in replies:
        if reply.poll_id in found or reply.body_sealed is None:
            continue
        try:
            text = sealing.open_text(key(reply.channel), reply.body_sealed,
                                     channel_id=reply.channel_id, message_id=reply.pk)
        except (sealing.SealBroken, ValueError):
            continue
        if needle in text.casefold():
            found.add(reply.poll_id)
    return found


def page(user, communities, *, community=None, mode="new", query="", number=1):
    """One page of threads that this member may read, never across access."""
    communities = list(communities)
    channels = [place.forum_channel for place in communities
                if hasattr(place, "forum_channel")]
    if community is not None:
        channels = [channel for channel in channels if channel.community_id == community.pk]
    now = timezone.now()
    rows = ChannelPoll.objects.filter(channel__in=channels, removed_at__isnull=True)
    if mode == "active":
        rows = rows.exclude(status=PollStatus.ARCHIVED)
    elif mode == "unanswered":
        answered = PollBallot.objects.filter(voter=user).values("poll_id")
        rows = rows.filter(status=PollStatus.OPEN, closes_at__gt=now).exclude(pk__in=answered)
    elif mode == "closed":
        rows = rows.filter(Q(status=PollStatus.CLOSED) |
                           Q(status=PollStatus.OPEN, closes_at__lte=now))
    elif mode == "archived":
        rows = rows.filter(status=PollStatus.ARCHIVED)
    else:
        mode = "new"
        rows = rows.exclude(status=PollStatus.ARCHIVED)
    query = " ".join(str(query or "").split())[:SEARCH_MAX]
    if len(query) >= 2:
        from toto.core import ratelimit

        try:
            ratelimit.check(f"forum:search:{user.pk}",
                            limit=posting.SEARCHES_PER_MINUTE, window=60)
        except ratelimit.RateLimited as exc:
            raise posting.Refusal(_("You are searching too fast. Wait a moment."),
                                  429, getattr(exc, "retry_after", None)) from None
        rows = rows.filter(pk__in=_matches(user, channels, query))
    rows = rows.select_related("channel__community")
    rows = rows.order_by("-last_activity_at", "-created_at") if mode == "active" else rows.order_by("-created_at")
    pager = Paginator(rows, PAGE_SIZE).get_page(number)
    grouped = {}
    for poll in pager.object_list:
        grouped.setdefault(poll.channel_id, []).append(poll)
    rendered = {}
    for group in grouped.values():
        channel = group[0].channel
        for row in voting.polls_to_dicts(
                group, key=posting._key(channel), user=user,
                moderator=access.is_administrator(user)):
            rendered[row["id"]] = row
    cards = []
    for poll in pager.object_list:
        card = rendered[str(poll.pk)]
        card["community_name"] = poll.channel.community.name
        card["community_slug"] = poll.channel.community.slug
        cards.append(card)
    return {"cards": cards, "page": pager, "mode": mode, "query": query}
