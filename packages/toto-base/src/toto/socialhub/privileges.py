"""What a person's institutions grant them — the one resolver everything asks.

**Rights are held by institutions, never by persons.** There are two, and they
grant in two different shapes:

* a **community** grants to *every member* — its privilege row;
* a **station** grants to *whoever currently holds it* — a federal office, and
  the headroom that office needs to do its work.

A person HOLDS the union of both. **Highest privilege always**: any community
granting a right grants it, any office granting a right grants it, and the
largest limit multiplier wins. That is not a loophole,
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
mid-migrate — every failure answers "no rights, no headroom" and never raises. A
gate failing open on a missing row would be privilege escalation.

**What used to be here.** A ``head_weight`` per community, and a bulk twin that
resolved a whole nightly levy run in two queries, because the head tax asked
this module for every user on the platform once a night. The head tax is gone —
what a community does to what its members owe now lives in
``toto.subscriptions`` as a discount on a plan, and nothing in this module is
called in bulk any more.
"""

from __future__ import annotations

from decimal import Decimal

from django.db import DatabaseError

#: Every named right an institution can grant — field names carried by BOTH
#: CommunityPrivilege and Station, so the two stay in step and a right cannot be
#: added to one and forgotten on the other. The money dial is deliberately not
#: in here: ``limit_multiplier`` is a number, not access, and is asked through
#: its own function below.
RIGHTS = (
    "may_see_community_chain",
    "may_administer_communities",
    "may_manage_community_news",
    "may_operate_mint",
)

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


def limit_multiplier_for(user) -> Decimal:
    """How much headroom this user's offices buy them, as a multiplier.

    The LARGEST across every office they hold, and never below 1 — an office
    adds room and can never take it away. This multiplies quota LIMITS only;
    prices never see it.
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
