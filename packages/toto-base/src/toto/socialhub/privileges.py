"""What a person's institutions grant them — the one resolver everything asks.

**Rights are held by institutions, never by persons.** There are two, and they
grant in two different shapes:

* a **community** grants to *every member* — its privilege row, and the head
  weight that decides what its members owe the federation;
* a **station** grants to *whoever currently holds it* — a federal office, and
  the headroom that office needs to do its work.

A person HOLDS the union of both. **Highest privilege always**: any community
granting a right grants it, any office granting a right grants it, the lowest
head weight wins, and the largest limit multiplier wins. That is not a loophole,
it is the granting mechanism — membership is invite-gated
(``MembershipApplication`` → accepted → ``communities.add`` is the only door)
and an office is appointed in admin, so admitting or appointing someone *is* the
grant, and expelling them or vacating the office *is* the revocation. Neither
can hand one named individual a right their successor will not inherit, which is
the property this module exists to preserve.

Grants live on :class:`~toto.socialhub.models.CommunityPrivilege` and
:class:`~toto.socialhub.models.Station`, both **edited in Django admin and
nowhere else**. A station's *existence* is public — the roster is the point of an
institution — but what it grants is not; no page renders or edits a capability,
and the gates that consume them simply work or refuse.

Before this the platform had the idea twice as single booleans —
``Community.is_federal_tribe`` (an exemption nothing implemented) and
``Person.is_federal_agent`` (help text about taxes, code about admin views) —
and three ad-hoc re-implementations of "get from a user to their communities",
one of which had never worked at all.

**Everything degrades to the commoner.** No person, anonymous, a database
mid-migrate — every failure answers "no rights, ordinary rate, no headroom" and
never raises. A gate failing open on a missing row would be privilege
escalation; a levy raising on one would stop the nightly run for everyone.

**Bulk first.** The nightly levy walks every holder on the platform with one
``in_bulk``; a per-user lookup would turn a two-query run into N. The
``*_for_users`` twins resolve a whole run in a fixed number of queries.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import DatabaseError

#: Every named right an institution can grant — field names carried by BOTH
#: CommunityPrivilege and Station, so the two stay in step and a right cannot be
#: added to one and forgotten on the other. The money dials are deliberately not
#: in here: ``head_weight`` and ``limit_multiplier`` are numbers, not access, and
#: are asked through their own functions below.
RIGHTS = (
    "may_see_community_chain",
    "may_administer_communities",
    "may_manage_community_news",
    "may_operate_mint",
)

#: The head weight of someone who belongs to no community, and of a community
#: with no privilege row. One head, the ordinary rate.
ORDINARY_HEAD_WEIGHT = Decimal("1")


def _person_of(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    try:
        person = getattr(user, "community_profile", None)
        if person is not None:
            return person
        from toto.people.models import Person

        return Person.objects.filter(user=user).first()
    except (DatabaseError, AttributeError):
        return None


def has_privilege(user, right: str) -> bool:
    """Does any community this user belongs to, or any office they hold, grant
    ``right``?

    ``right`` must be a member of :data:`RIGHTS`; anything else raises rather
    than quietly answering False forever — a mistyped right that returns False
    is a gate nobody can pass and nobody can find.

    The community is asked first because it is the common case and the query is
    cheaper; the office is asked only when the community says no.
    """
    if right not in RIGHTS:
        raise ValueError(f"{right!r} is not a privilege; see "
                         "socialhub.privileges.RIGHTS")
    person = _person_of(user)
    if person is None:
        return False
    try:
        if person.communities.filter(**{f"privilege__{right}": True}).exists():
            return True
        from .models import Station

        return Station.objects.filter(
            holder=person, active=True, **{right: True}).exists()
    except DatabaseError:
        return False


def head_weight_for(user) -> Decimal:
    """How many heads this user counts as for the federal head tax.

    The LOWEST weight across their communities wins: belonging to a trusted
    community is what makes you cheap to tax, and belonging to a second one can
    only help. Someone in no community pays the ordinary rate — this is a tax on
    everyone, not a penalty for being unaffiliated.

    Offices do not enter into it. A station holder pays exactly what anyone else
    in their community pays, which is the whole of "extra limits, same taxes".
    """
    person = _person_of(user)
    if person is None:
        return ORDINARY_HEAD_WEIGHT
    try:
        weights = list(person.communities
                       .values_list("privilege__head_weight", flat=True))
    except DatabaseError:
        return ORDINARY_HEAD_WEIGHT
    if not weights:
        # Nobody's community, so nobody's rate but the ordinary one. This is a
        # tax on everyone, not a penalty for being unaffiliated.
        return ORDINARY_HEAD_WEIGHT
    # A community with no privilege row IS an ordinary community, and belonging
    # to one is itself a way out of a heavy one — so it enters the comparison
    # as a 1 rather than being skipped. Lowest wins.
    return min(ORDINARY_HEAD_WEIGHT if w is None else w for w in weights)


def limit_multiplier_for(user) -> Decimal:
    """How much headroom this user's offices buy them, as a multiplier.

    The LARGEST across every office they hold, and never below 1 — an office
    adds room and can never take it away. This multiplies quota LIMITS only;
    prices and the head tax never see it.
    """
    person = _person_of(user)
    if person is None:
        return Decimal("1")
    try:
        from .models import Station

        best = (Station.objects
                .filter(holder=person, active=True)
                .order_by("-limit_multiplier")
                .values_list("limit_multiplier", flat=True)
                .first())
    except DatabaseError:
        return Decimal("1")
    if best is None:
        return Decimal("1")
    return max(best, Decimal("1"))


# ---------------------------------------------------------------------------
# Bulk twin — the nightly run resolves a whole host in a fixed query count
# ---------------------------------------------------------------------------

def head_weights_for_users(user_ids) -> dict:
    """``{user_id: head_weight}`` for a whole levy run. Two queries.

    Only ids that differ from :data:`ORDINARY_HEAD_WEIGHT` appear; the caller
    uses the ordinary weight for everyone else, so the common case — no
    community setting a weight at all — costs two cheap queries and adds no rows.
    """
    ids = list(user_ids)
    if not ids:
        return {}
    try:
        from toto.people.models import Person

        from .models import CommunityPrivilege

        # Which communities set a weight other than the ordinary one? Usually
        # none or few, and reading them first keeps the join below small.
        weighted = dict(CommunityPrivilege.objects
                        .exclude(head_weight=ORDINARY_HEAD_WEIGHT)
                        .values_list("community_id", "head_weight"))
        if not weighted:
            return {}
        memberships = (Person.objects
                       .filter(user_id__in=ids)
                       .values_list("user_id", "communities"))
    except DatabaseError:
        return {}

    best: dict = {}
    seen_ordinary: set = set()
    for uid, cid in memberships:
        if uid is None:
            continue
        weight = weighted.get(cid)
        if weight is None:
            # A community with no weight of its own is an ordinary one, and
            # belonging to it caps this person at the ordinary rate however
            # heavy their other communities are. Lowest wins.
            seen_ordinary.add(uid)
            continue
        if uid not in best or weight < best[uid]:
            best[uid] = weight

    return {uid: weight for uid, weight in best.items()
            if uid not in seen_ordinary and weight != ORDINARY_HEAD_WEIGHT}
