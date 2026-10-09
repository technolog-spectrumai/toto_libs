"""The one channel of a community: made once with its encryption key.

``ensure_channel(community)`` is the only place a channel is made, and it is
idempotent: ``manage.py ingress_forum`` calls it for every community there
is, and the channel page calls it on the first opening by a member who may
read it (a community made after the last deploy). The database refuses a
second channel for one community (the one-to-one), so a race ends with one
row and the loser reads the winner's.

"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from . import keys

def channel_of(community):
    """The community's channel, or None. Makes nothing."""
    from .models import ForumChannel

    return ForumChannel.objects.filter(community=community).first()


def ensure_channel(community):
    """The community's channel, made with its key if absent.

    Raises ``keys.ChannelKeyUnavailable`` when the forum's secret is missing:
    a channel is never left without a key, so nothing could be stored in
    clear later for want of one.
    """
    from .models import ForumChannel

    channel = channel_of(community)
    if channel is None:
        try:
            with transaction.atomic():
                channel = ForumChannel.objects.create(community=community)
                keys.ensure_key(channel)
        except IntegrityError:
            channel = ForumChannel.objects.get(community=community)
    keys.ensure_key(channel)
    return channel


def next_seq(channel) -> int:
    """Take the channel's next event number. Call inside a transaction: the
    channel's row stays locked until it commits, so events commit in their
    order and a page that has seen event N has seen every event before it."""
    from .models import ForumChannel

    row = ForumChannel.objects.select_for_update().only("last_seq").get(pk=channel.pk)
    row.last_seq += 1
    row.save(update_fields=["last_seq"])
    channel.last_seq = row.last_seq
    return row.last_seq
