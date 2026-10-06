"""Who sees a person's point, who sets a community's headquarters, and what
of everything on the map one member may see.

``visible_point`` serves the profile page and every door, so a page and a
door cannot disagree. The one location-visibility setting of a person is
``Person.show_address``, read through ``contact_access.may_see_address``: it
governs the point exactly as it governs the address text.

``visible_to(user)`` (stage 64, 2026-10-06) is "all data together": the one
answer to what the Locations app shows a member, used by the page and by
every door that names a row.

    people          the member's own point; another person's only where
                    ``visible_point`` gives it (their "show address" switch,
                    or an administrator)
    headquarters    every community's headquarters and zone, as on its page
    pins, zones     the contributions of the communities the member belongs
                    to (``is_community_member``: a member, the head, a senior
                    member); of every community for an administrator (a real
                    superuser on the Superuser plan, never staff alone)
    hidden rows     a contribution a moderator hid: only for its author and
                    for who may moderate that community (its head, an
                    administrator)

Nothing else reaches the page. A row a member may not see answers 404 at
every door, as if it were not there; what a member may see and not do is 403.
"""

from __future__ import annotations

#: The most rows of one kind the Locations page is handed.
ROW_CAP = 1000


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


# ---------------------------------------------------------------------------
# Stage 64: all data together
# ---------------------------------------------------------------------------

def may_contribute(user, community) -> bool:
    """May ``user`` see and add to ``community``'s pins and zones: one of its
    members (``is_community_member``), or an administrator."""
    from toto.socialhub.contact_access import is_administrator
    from toto.socialhub.permissions import is_community_member

    return is_community_member(user, community) or is_administrator(user)


def may_moderate(user, community) -> bool:
    """The community's head, or an administrator: hides, restores and deletes
    its contributions and withdraws a comment under them. Never edits another
    member's words. Staff alone is not enough, nor a senior member."""
    from toto.socialhub.permissions import may_moderate_community

    return may_moderate_community(user, community)


def may_edit(user, row) -> bool:
    """A contribution is edited by its author and by nobody else."""
    return (getattr(user, "is_authenticated", False)
            and row.author_id is not None and row.author_id == user.pk)


class Visible:
    """What one member may see of everything on the map. Made by
    ``visible_to``; every list and every single row comes from here."""

    def __init__(self, user):
        from django.db.models import Q

        from toto.people.models import Person
        from toto.socialhub.contact_access import is_administrator
        from toto.socialhub.models import Community

        self.user = user
        self.signed_in = bool(getattr(user, "is_authenticated", False))
        self.administrator = self.signed_in and is_administrator(user)
        self.person = Person.objects.filter(user=user).first() if self.signed_in else None
        #: The communities whose contributions the member sees, and those
        #: the member moderates. None stands for "every community".
        self.community_ids = None
        self.moderated_ids = None
        if not self.administrator:
            self.community_ids, self.moderated_ids = set(), set()
            if self.person is not None:
                self.moderated_ids = set(
                    Community.objects.filter(head=self.person).values_list("pk", flat=True))
                self.community_ids = set(
                    Community.objects.filter(
                        Q(pk__in=self.person.communities.values("pk"))
                        | Q(head=self.person) | Q(senior_members=self.person))
                    .values_list("pk", flat=True))

    # -- communities -----------------------------------------------------

    def communities(self):
        """The communities whose pins and zones the member sees and may add
        to, by name."""
        from toto.socialhub.models import Community

        rows = Community.objects.order_by("name")
        if self.community_ids is not None:
            rows = rows.filter(pk__in=self.community_ids)
        return rows

    def moderates(self, community_id) -> bool:
        return self.moderated_ids is None or community_id in self.moderated_ids

    # -- contributions ---------------------------------------------------

    def _contributions(self, model, geometry):
        from django.db.models import Q

        rows = model.objects.select_related("community", "author", geometry)
        if not self.signed_in:
            return rows.none()
        if self.administrator:
            return rows
        return rows.filter(community_id__in=self.community_ids).filter(
            Q(hidden_at__isnull=True) | Q(author=self.user)
            | Q(community_id__in=self.moderated_ids))

    def pins(self):
        from .models import CommunityPin

        return self._contributions(CommunityPin, "address")

    def zones(self):
        from .models import CommunityZone

        return self._contributions(CommunityZone, "zone")

    def pin(self, uid, community=None):
        """The pin named ``uid`` if this member may see it, else None. With
        ``community`` only a pin of that community."""
        rows = self.pins().filter(uid=uid)
        if community is not None:
            rows = rows.filter(community=community)
        return rows.first()

    def zone(self, uid, community=None):
        rows = self.zones().filter(uid=uid)
        if community is not None:
            rows = rows.filter(community=community)
        return rows.first()

    # -- people and headquarters ------------------------------------------

    def people(self):
        """``PersonAddress`` rows: the member's own, and those their owner
        shows. The same answer ``visible_point`` gives, person by person."""
        from django.db.models import Q

        from .models import PersonAddress

        rows = PersonAddress.objects.select_related("person", "address")
        if not self.signed_in:
            return rows.none()
        if self.administrator:
            return rows
        return rows.filter(Q(person__show_address=True) | Q(person__user=self.user))

    def headquarters(self):
        """Every community's headquarters and zone: each signed-in member
        sees them, as on the community's page."""
        from .models import CommunityHeadquarters

        rows = CommunityHeadquarters.objects.select_related("community", "address", "zone")
        return rows if self.signed_in else rows.none()


def visible_to(user) -> Visible:
    """All data together, for ``user``: see the module's docstring."""
    return Visible(user)
