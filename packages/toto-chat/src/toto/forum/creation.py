"""Making a room, and joining one — the rules, once, for the page and the API.

Both doors (views.ChannelCreateView and api_views.ChannelListApiView.post)
and both join doors call these, so the password, invite, encryption, expiry,
rate-limit and billing rules cannot drift between them.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext as _

#: How long a temporary room may be asked to live.
EXPIRY_CHOICES = {"": None, "1h": timedelta(hours=1), "24h": timedelta(hours=24),
                  "7d": timedelta(days=7), "30d": timedelta(days=30)}
MIN_PASSWORD = 8
#: What a NEW room may be. "invite" is legacy: existing rows keep it.
NEW_ROOM_ACCESS = ("open", "password")

#: (limit, window seconds). Password attempts are the costly ones — each is an
#: Argon2id derivation at 64 MiB — so they are limited per person per room and
#: per room overall. Settings may override: FORUM_RATE_LIMITS.
DEFAULT_LIMITS = {"password_user": (5, 300), "password_room": (30, 300),
                  "send": (20, 10), "api_post": (30, 60)}


def limits() -> dict:
    from django.conf import settings

    return {**DEFAULT_LIMITS, **(getattr(settings, "FORUM_RATE_LIMITS", None) or {})}


class RoomRefused(Exception):
    """A room that cannot be made or joined, with a sentence for the person."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def create_room(user, *, name: str, access: str = "open", password: str = "",
                encrypted: bool = False, expires_in: str = ""):
    from toto.people.models import Person

    from . import billing, rooms
    from .models import ForumChannel, ForumMember

    name = (name or "").strip()
    if not name:
        raise RoomRefused(_("A channel needs a name."))
    slug = slugify(name)[:50]
    if not slug:
        raise RoomRefused(_("That name cannot be turned into a URL slug."))
    if slug in ForumChannel.RESERVED_SLUGS:
        raise RoomRefused(_("“%(name)s” is one of the forum's own addresses. "
                            "A room with that name could never be opened.") % {"name": name})
    if ForumChannel.objects.filter(name=name).exists() or ForumChannel.objects.filter(slug=slug).exists():
        raise RoomRefused(_("A channel called “%(name)s” already exists.") % {"name": name}, 409)
    if ForumChannel.at_capacity():
        raise RoomRefused(_("This platform holds at most %(n)s rooms, and it has that many. "
                            "Close one before opening another.") % {"n": ForumChannel.max_channels()}, 409)
    # Two kinds of access since 2026-09-26: open, or a password. Invite-only
    # rooms are no longer made; the ones that exist keep their rule (they are
    # still joined only by being added), so nobody's private room opens up.
    if access not in NEW_ROOM_ACCESS:
        raise RoomRefused(_("Choose who may join: open or password."))
    if access == "password" and len(password or "") < MIN_PASSWORD:
        raise RoomRefused(_("A room password is at least %(n)s characters.") % {"n": MIN_PASSWORD})
    if expires_in not in EXPIRY_CHOICES:
        raise RoomRefused(_("Choose how long the room lives."))
    lifetime = EXPIRY_CHOICES[expires_in]
    if lifetime is not None and encrypted and not rooms.shared_key_store_available():
        raise RoomRefused(_("A temporary encrypted room needs the shared cache, and this "
                            "server has none; make it permanent or unencrypted."))
    if encrypted:
        billing.check_room_key_affordable(user)

    with transaction.atomic():
        channel = ForumChannel(name=name, slug=slug, created_by=user, access=access,
                               is_encrypted=bool(encrypted),
                               expires_at=(timezone.now() + lifetime) if lifetime else None)
        if access == "password":
            rooms.set_password(channel, password)
        channel.save()
        if encrypted:
            rooms.create_room_key(channel, password=password if access == "password" else None)
            billing.settle_room_key(user, channel)
        person = Person.objects.filter(user=user).first()
        if person:
            ForumMember.objects.create(channel=channel, person=person, is_active=True)
    return channel


def join(user, channel, *, password: str = ""):
    """Make ``user`` an active member, or raise RoomRefused. Returns created."""
    from toto.core import ratelimit
    from toto.people.models import Person

    from . import permissions, rooms
    from .models import ForumMember

    person = Person.objects.filter(user=user).first()
    if not person:
        raise RoomRefused(_("Your user is not linked to a person profile, so you can only "
                            "observe this channel."), 403)
    verdict = permissions.join_verdict(user, channel)
    already = ForumMember.objects.filter(channel=channel, person=person, is_active=True).exists()
    if already:
        return False
    if verdict == "closed":
        raise RoomRefused(_("This room has expired."), 410)
    if verdict == "invite":
        raise RoomRefused(_("This room is invite only; its creator or staff add members."), 403)
    if verdict == "password":
        lim = limits()
        user_limit, user_window = lim["password_user"]
        room_limit, room_window = lim["password_room"]
        try:
            ratelimit.check(f"forum:pw:{channel.pk}:{user.pk}", limit=user_limit, window=user_window)
            ratelimit.check(f"forum:pw:{channel.pk}", limit=room_limit, window=room_window)
        except ratelimit.RateLimited as exc:
            raise RoomRefused(_("Too many password attempts. Try again in %(s)s seconds.")
                              % {"s": exc.retry_after}, 429) from exc
        if not rooms.verify_password(channel, password):
            raise RoomRefused(_("That is not the room's password."), 403)
    member, created = ForumMember.objects.get_or_create(
        channel=channel, person=person, defaults={"is_active": True})
    if not member.is_active:
        member.is_active = True
        member.save(update_fields=["is_active"])
    return created


def add_member(actor, channel, username: str):
    from django.contrib.auth import get_user_model

    from toto.people.models import Person

    from . import permissions
    from .models import ForumMember

    if not permissions.can_manage_members(actor, channel):
        raise RoomRefused(_("Only the room's creator or staff add members."), 403)
    user = get_user_model().objects.filter(username=(username or "").strip()).first()
    person = Person.objects.filter(user=user).first() if user else None
    if person is None:
        raise RoomRefused(_("No member with that username."), 404)
    member, _created = ForumMember.objects.get_or_create(
        channel=channel, person=person, defaults={"is_active": True})
    if not member.is_active:
        member.is_active = True
        member.save(update_fields=["is_active"])
    return member


def remove_member(actor, channel, member_pk):
    from . import permissions
    from .models import ForumMember

    if not permissions.can_manage_members(actor, channel):
        raise RoomRefused(_("Only the room's creator or staff remove members."), 403)
    member = ForumMember.objects.filter(channel=channel, pk=member_pk, is_active=True).first()
    if member is None:
        raise RoomRefused(_("No such member."), 404)
    # Per instance, so signals.py tells a live socket it was revoked.
    member.is_active = False
    member.save(update_fields=["is_active"])
    return member


def change_password(actor, channel, password: str):
    from . import permissions, rooms

    if not permissions.can_manage_members(actor, channel):
        raise RoomRefused(_("Only the room's creator or staff change the password."), 403)
    if channel.access != "password":
        raise RoomRefused(_("This room has no password."))
    if len(password or "") < MIN_PASSWORD:
        raise RoomRefused(_("A room password is at least %(n)s characters.") % {"n": MIN_PASSWORD})
    room_key = rooms.open_key(channel) if channel.is_encrypted else None
    rooms.set_password(channel, password, room_key=room_key)
    channel.save(update_fields=["password_salt", "password_verifier", "kdf_memory_cost",
                                "kdf_iterations", "kdf_lanes"])
