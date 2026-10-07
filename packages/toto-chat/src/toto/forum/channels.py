"""The one channel of a community: made once, with its key and its bucket.

``ensure_channel(community)`` is the only place a channel is made, and it is
idempotent: ``manage.py ingress_forum`` calls it for every community there
is, and the channel page calls it on the first opening by a member who may
read it (a community made after the last deploy). The database refuses a
second channel for one community (the one-to-one), so a race ends with one
row and the loser reads the winner's.

The bucket is the channel's own (``ensure_bucket``): the vault has no
community bucket to reuse, so one local bucket per channel is made, named
after the community, with NO owner — the vault's "bucket owner" clause then
opens it to nobody, and what the forum keeps in it is sealed anyway.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from . import keys

#: What the bucket's name ends with, after the community's name.
BUCKET_SUFFIX = " — forum"


def channel_of(community):
    """The community's channel, or None. Makes nothing."""
    from .models import ForumChannel

    return ForumChannel.objects.filter(community=community).first()


def ensure_channel(community):
    """The community's channel, made with its key and its bucket if absent.

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
    if channel.bucket_id is None:
        ensure_bucket(channel)
    return channel


def ensure_bucket(channel):
    """The channel's bucket, made if absent (or if the vault's side was
    deleted). A free name and slug are found as ``vault.personal_bucket``
    finds them."""
    from toto.vault.models import Bucket, StorageBackend

    if channel.bucket_id is not None:
        bucket = Bucket.objects.filter(pk=channel.bucket_id).first()
        if bucket is not None and not bucket.is_being_deleted:
            return bucket
    community = channel.community
    base_slug = f"forum-{community.slug}"[:110]
    base_name = f"{community.name[:80]}{BUCKET_SUFFIX}"
    for n in range(1, 100):
        slug = base_slug if n == 1 else f"{base_slug}-{n}"
        name = base_name if n == 1 else f"{base_name} ({n})"
        if Bucket.objects.filter(slug=slug).exists() or Bucket.objects.filter(name=name).exists():
            continue
        try:
            with transaction.atomic():
                bucket = Bucket.objects.create(owner=None, slug=slug, name=name,
                                               storage_backend=StorageBackend.LOCAL)
        except IntegrityError:
            continue
        channel.bucket = bucket
        channel.save(update_fields=["bucket"])
        return bucket
    raise RuntimeError(f"No free bucket slug for the channel of {community.slug}.")


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
