"""Who may read, post, moderate — the one place every door asks.

    signed in           anonymous gets nothing, not even the list
    entitled            the ``forum`` entitlement on the member's plan: never
                        on Free. The plan gate's middleware asks the same of
                        the URL namespace; this asks again, so a host with
                        the middleware switched off still refuses
    may_read            entitled, and a member of the community (a member, a
                        senior member or its head), or an administrator
    may_post            may_read
    may_moderate        entitled, and the community's head or an
                        administrator. Never staff alone; never a senior
                        member
    may_remove_message  may_read, and its sender or who may moderate
    may_manage_poll     may_read, and who opened it or who may moderate

Membership IS the community's (``socialhub.permissions.is_community_member``):
a channel has no member list of its own, so leaving a community closes its
channel at the next request. An administrator is
``socialhub.contact_access.is_administrator``: a real superuser on the
Superuser plan where the host sells it.
"""

from __future__ import annotations

from django.apps import apps

#: The entitlement, which is also the URL namespace the plan gate reads.
FEATURE = "forum"


def signed_in(user) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False))


def entitled(user) -> bool:
    """Does the member's plan carry the forum? True on a host that sells no
    plans at all."""
    if not signed_in(user):
        return False
    if not apps.is_installed("toto.subscriptions"):
        return True
    from toto.subscriptions.gate import is_entitled

    try:
        return bool(is_entitled(user, FEATURE))
    except Exception:  # noqa: BLE001 - a plan that cannot be read opens nothing
        return False


def is_administrator(user) -> bool:
    from toto.socialhub.contact_access import is_administrator as answer

    return bool(answer(user))


def is_member(user, community) -> bool:
    from toto.socialhub.permissions import is_community_member

    return bool(is_community_member(user, community))


def may_read(user, community) -> bool:
    if community is None or not entitled(user):
        return False
    return is_member(user, community) or is_administrator(user)


def may_post(user, community) -> bool:
    return may_read(user, community)


def may_moderate(user, community) -> bool:
    if community is None or not entitled(user):
        return False
    from toto.socialhub.permissions import may_moderate_community

    return bool(may_moderate_community(user, community))


def may_remove_message(user, message) -> bool:
    community = message.channel.community
    if not may_read(user, community):
        return False
    if message.sender_id is not None and message.sender_id == user.pk:
        return True
    return may_moderate(user, community)


def may_manage_poll(user, poll) -> bool:
    community = poll.channel.community
    if not may_read(user, community):
        return False
    if poll.created_by_id is not None and poll.created_by_id == user.pk:
        return True
    return is_administrator(user)


def communities_of(user):
    """The communities whose channel ``user`` may read, by name: every
    community for an administrator, else the ones they belong to. Empty for
    who is not entitled."""
    from django.db.models import Q

    from toto.people.models import Person
    from toto.socialhub.models import Community

    if not entitled(user):
        return Community.objects.none()
    if is_administrator(user):
        return Community.objects.order_by("name")
    person = Person.objects.filter(user=user).first()
    if person is None:
        return Community.objects.none()
    return (Community.objects
            .filter(Q(pk__in=person.communities.values("pk")) | Q(head=person)
                    | Q(senior_members=person))
            .distinct().order_by("name"))
