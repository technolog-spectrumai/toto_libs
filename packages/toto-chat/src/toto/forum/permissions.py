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


def _live(channel):
    """The channel row, and whether it is past its expiry (a slug is looked up)."""
    from .models import ForumChannel

    if isinstance(channel, str):
        channel = ForumChannel.objects.filter(slug=channel).first()
    return channel, (channel is not None and channel.is_expired)


def can_read(user, channel):
    """May the user read this channel's message history and search it?

    An expired temporary room refuses everybody from the instant it expires,
    before the sweep has deleted it — the clock, not the sweep, is the rule.
    """
    row, expired = _live(channel)
    return row is not None and not expired and is_member(user, row)


def can_send(user, channel):
    """May the user post to this channel?"""
    return can_read(user, channel)


def join_verdict(user, channel) -> str:
    """"open" or "password" — how this user may join ("closed" once expired)."""
    if channel.is_expired:
        return "closed"
    if channel.access == "password":
        return "password"
    return "open"


def can_manage_members(user, channel) -> bool:
    """The room's creator, or staff, adds and removes members."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return is_operator(user) or (channel.created_by_id is not None
                                 and channel.created_by_id == user.id)


def listable_channels(user):
    """Channels a person may see listed: every room. Invite-only rooms, the
    one kind hidden from non-members, are gone since 2026-09-28 — a password
    keeps a room's content private, and its name was never the secret the
    password guards. Kept as the one door the list and the API ask."""
    from .models import ForumChannel

    return ForumChannel.objects.all()


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


def is_operator(user) -> bool:
    """The platform's operator predicate, verbatim.

    ``is_superuser`` does not imply ``is_staff`` in Django, so both count —
    bare ``is_staff`` once locked superusers out of the quota desk. Kept here,
    in this app's stated single door, rather than as a fifth private copy of
    the same three lines.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    return bool(getattr(user, "is_staff", False)
                or getattr(user, "is_superuser", False))


def require_operator(request):
    """403 for anybody who is not staff.

    The gate of the forum's staff desks: ``/forum/cleanup/`` (the platform's
    retention, back since 2026-10-02) and each room's Settings and Archive
    tabs. 403 and not 404: the 404 rule protects a URL that contains a secret
    — a room slug somebody could enumerate. ``/forum/cleanup/`` is a fixed
    path and knowing it exists discloses nothing, and a room tab's slug is
    one the caller already knows. The links are hidden from non-staff as
    well, because a page that always answers 403 is worse than no link.
    """
    from django.core.exceptions import PermissionDenied
    from django.utils.translation import gettext as _

    if not is_operator(getattr(request, "user", None)):
        raise PermissionDenied(_("Forum cleanup is staff only."))
    return request.user


def require_member(request, channel):
    """The gate every room tab opens with. Returns the active membership.

    One function rather than a check at each view, for the reason this
    module's docstring records: the last time membership had two sources,
    they disagreed and a removed user kept posting.
    """
    from django.core.exceptions import PermissionDenied
    from django.utils.translation import gettext as _

    member = member_for(request.user, channel)
    if member is None:
        raise PermissionDenied(_("Join this room to see its pages."))
    return member
