"""Who sees a person's point, and who sets a community's headquarters.

``visible_point`` serves the profile page and every door, so a page and a
door cannot disagree. The one location-visibility setting of a person is
``Person.show_address``, read through ``contact_access.may_see_address``: it
governs the point exactly as it governs the address text.
"""

from __future__ import annotations


def own_point(person):
    """The person's ``Address`` row, or None. For the owner's own doors."""
    from .models import PersonAddress

    if person is None or not getattr(person, "pk", None):
        return None
    link = PersonAddress.objects.select_related("address").filter(person=person).first()
    return link.address if link is not None else None


def visible_point(viewer, person):
    """The ``Address`` ``viewer`` may see as ``person``'s point, or None: no
    point, or one its owner does not show (``Person.show_address``; the owner
    and an administrator always see it)."""
    from toto.socialhub.contact_access import may_see_address

    if person is None or not getattr(viewer, "is_authenticated", False):
        return None
    if not may_see_address(viewer, person):
        return None
    return own_point(person)


def headquarters_of(community):
    """``(address, zone)`` of a community, each a row or None."""
    from .models import CommunityHeadquarters

    link = (CommunityHeadquarters.objects.select_related("address", "zone")
            .filter(community=community).first())
    if link is None:
        return None, None
    return link.address, link.zone


def may_set_headquarters(user, community) -> bool:
    """The community's head, or an administrator
    (``socialhub.permissions.may_moderate_community``)."""
    from toto.socialhub.permissions import may_moderate_community

    return may_moderate_community(user, community)
