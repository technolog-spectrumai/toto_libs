"""Who joined, who signed in, who signed out (2026-10-04).

The owner: "if we have websockets also add notifications that other user
joined, logged in, logged out".

**Who hears it: the members of the SAME COMMUNITIES, and nobody else.**
``audience(person)`` is every account whose profile shares at least one
community with the person (``Person.communities``, read both ways) — not the
person, not staff, not a superuser who shares none. Somebody in no community
is announced to nobody.

* **Joined** — a person became a member of a community
  (``Person.communities`` gained a row, from either side: an approved
  application, the admin, the console). The other members of THAT community
  get a notification kept in the bell (``community.joined``), linking to the
  profile.
* **Signed in / signed out** — toasts only. Nothing is written: the message
  goes to the audience's open pages over the live socket and is gone; there
  is no presence list and no "who is online" page. Sign-in is Django's
  ``user_logged_in`` — once when a session starts, never per request.
  Sign-out is ``user_logged_out`` and a session the member ended themselves
  (``toto.core.user_sessions.session_ended``); a session that only expired is
  not announced, because nothing happens when it does.
* **The switch.** ``Person.show_online`` ("Show others when I am online", the
  Edit profile tab, on by default) turns one's own sign-in and sign-out
  announcements off. It does not hide a joining: who is in a community is
  on the community's page.
* **Throttle.** One sign-in toast (and one sign-out toast) per person per
  listener per ``THROTTLE_SECONDS``, held in the cache: somebody signing in
  on three devices is one toast.

Profiles have no hidden or blocked state on this platform — every signed-in
member may open every profile — so the name and the profile link say no more
than the community's member list already does to the same people.

Never in the way: every receiver swallows and logs the error's class.
"""

from __future__ import annotations

import logging

from django.apps import apps

from . import kinds
from .services import send
from .sources import _url, quiet

log = logging.getLogger("toto.notify")

#: Seconds between two toasts of one kind about one person to one listener.
THROTTLE_SECONDS = 300


def person_of(user):
    """The account's profile, read afresh: the switch may have been turned
    since this ``user`` object was loaded."""
    from toto.people.models import Person

    if user is None or not getattr(user, "pk", None):
        return None
    return Person.objects.filter(user_id=user.pk).first()


def audience(person, *, communities=None):
    """The accounts told about ``person``: active accounts whose profile
    shares a community with them (``communities`` narrows it to those
    communities), the person's own account left out."""
    from django.contrib.auth import get_user_model

    if person is None:
        return get_user_model()._default_manager.none()
    shared = communities if communities is not None else person.communities.all()
    return (get_user_model()._default_manager
            .filter(is_active=True, community_profile__communities__in=shared)
            .exclude(pk=person.user_id).distinct())


def profile_link(person) -> str:
    return _url("socialhub:profile_details", person.slug) if person.slug else ""


def _throttled(event: str, person, listener_pk) -> bool:
    """True when this listener heard this of this person within the window."""
    from django.core.cache import cache

    key = f"notify.presence.{event}.{person.pk}.{listener_pk}"
    try:
        return not cache.add(key, 1, THROTTLE_SECONDS)
    except Exception:  # noqa: BLE001 - no cache: say it rather than lose it
        return False


def announce(user, event: str) -> int:
    """Tell ``user``'s audience they signed ``in`` or ``out``; how many
    were told. Nothing is stored."""
    from toto.core import live

    person = person_of(user)
    if person is None or not person.show_online or not live.available():
        return 0
    message = {"type": live.PRESENCE, "event": event,
               "name": person.display_name or user.get_username(),
               "link": profile_link(person)}
    told = 0
    for listener_pk in audience(person).values_list("pk", flat=True):
        if _throttled(event, person, listener_pk):
            continue
        live.publish(live.user_group(listener_pk), message)
        told += 1
    return told


@quiet
def on_logged_in(sender, request=None, user=None, **kwargs):
    announce(user, "in")


@quiet
def on_logged_out(sender, request=None, user=None, **kwargs):
    announce(user, "out")


@quiet
def on_session_ended(sender, user=None, **kwargs):
    announce(user, "out")


@quiet
def on_communities(sender, instance, action, reverse, model, pk_set, **kwargs):
    """``Person.communities`` gained rows: tell each community's other
    members, in the bell."""
    if action != "post_add" or not pk_set:
        return
    from toto.people.models import Person
    from toto.socialhub.models import Community

    if reverse:         # instance is the community; the pks are people
        pairs = [(person, instance) for person in Person.objects.filter(pk__in=pk_set)]
    else:               # instance is the person; the pks are communities
        pairs = [(instance, community) for community in
                 Community.objects.filter(pk__in=pk_set)]
    for person, community in pairs:
        actor = person.user if person.user_id else None
        for listener in audience(person, communities=[community]):
            send(listener, kinds.COMMUNITY_JOINED.key, actor=actor,
                 once=f"joined:{person.pk}:{community.pk}", link=profile_link(person),
                 name=person.display_name, community=community.name)


def connect() -> None:
    if not (apps.is_installed("toto.people") and apps.is_installed("toto.socialhub")):
        return
    from django.contrib.auth.signals import user_logged_in, user_logged_out
    from django.db.models.signals import m2m_changed

    from toto.core.user_sessions import session_ended
    from toto.people.models import Person

    uid = "toto.notify.presence."
    user_logged_in.connect(on_logged_in, dispatch_uid=uid + "in")
    user_logged_out.connect(on_logged_out, dispatch_uid=uid + "out")
    session_ended.connect(on_session_ended, dispatch_uid=uid + "ended")
    m2m_changed.connect(on_communities, sender=Person.communities.through,
                        dispatch_uid=uid + "joined")
