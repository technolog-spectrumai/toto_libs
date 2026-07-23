"""The single source of truth for who may read, send and moderate in a channel.

Before this module there were two disagreeing membership checks: the HTML view required a
row in the ``participants`` M2M *and* an active member row, while the websocket consumer
required only the member row. A user removed from ``participants`` saw a disabled composer
but could still post over a raw socket. ``participants`` is gone; ``ForumMember`` is the
only membership record, and every entry point routes through here.

Access model:

* anonymous — nothing, not even the channel list
* signed in — may browse channels and join them
* active member — may read history, search, and post
"""


def person_for(user):
    """The ``people.Person`` linked to ``user``, or ``None``."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    from toto.people.models import Person

    return Person.objects.filter(user=user).first()


def member_for(user, channel):
    """The user's active ``ForumMember`` row in ``channel``, or ``None``.

    ``channel`` may be a model instance or a slug.
    """
    person = person_for(user)
    if not person:
        return None

    from .models import ForumMember

    qs = ForumMember.objects.filter(person=person, is_active=True)
    if isinstance(channel, str):
        qs = qs.filter(channel__slug=channel)
    else:
        qs = qs.filter(channel=channel)
    return qs.select_related("person", "channel").first()


def is_member(user, channel):
    return member_for(user, channel) is not None


def can_browse(user):
    """May the user see the channel list and channel pages at all?"""
    return bool(user and getattr(user, "is_authenticated", False))


def can_read(user, channel):
    """May the user read this channel's message history and search it?"""
    return is_member(user, channel)


def can_send(user, channel):
    """May the user post to this channel?"""
    return is_member(user, channel)


def can_moderate(user, message):
    """May the user edit or delete this specific message?

    Authors may edit and delete their own; staff may delete anything.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if message.sender_id and message.sender_id == user.id:
        return True
    return bool(getattr(user, "is_staff", False))


def readable_channels(user):
    """Channels whose messages ``user`` may read — the scope for search."""
    from .models import ForumChannel

    person = person_for(user)
    if not person:
        return ForumChannel.objects.none()
    return ForumChannel.objects.filter(
        forum_members__person=person,
        forum_members__is_active=True,
    ).distinct()
