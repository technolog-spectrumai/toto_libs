"""What a person's communities grant them — the one resolver everything asks.

**Rights are held by institutions, never by persons.** A community grants to
*every member*, through its privilege row.

**Highest privilege always**: any community granting a right grants it. That is
not a loophole, it is the granting mechanism — membership is invite-gated
(``MembershipApplication`` → accepted → ``communities.add`` is the only door),
so admitting someone *is* the grant and expelling them *is* the revocation. It
cannot hand one named individual a right their successor will not inherit, which
is the property this module exists to preserve.

Grants live on :class:`~toto.socialhub.models.CommunityPrivilege`, **edited in
Django admin and nowhere else**. No page renders or edits a capability, and the
gates that consume them simply work or refuse.

**There used to be a second institution.** ``Station`` — a "Special Role" —
granted the same four rights to whoever held the office, and carried a
``limit_multiplier`` that bought that office extra quota headroom. It fused an
office, an authorisation grant and a payslip into one row, and it was removed in
8/2026 with the treasury payroll that paid it. Rights come from communities
only; recurring payment is a Faucet, which pays people and grants nothing.

Before this the platform had the idea twice as single booleans —
``Community.is_federal_tribe`` (an exemption nothing implemented) and
``Person.is_federal_agent`` (help text about taxes, code about admin views) —
and three ad-hoc re-implementations of "get from a user to their communities",
one of which had never worked at all.

**A circle grants nothing** (2026-09-28). Circles decide who reads wiki pages
and are joined through the admin, not by application; a right is held through
a functional community only, so :func:`has_privilege` skips circles even where
a privilege row names one (the model refuses to save such a row).

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


from django.db import DatabaseError

#: Every named right an institution can grant — the field names carried by
#: CommunityPrivilege. It was a shared vocabulary between that model and
#: Station, so a right could not be added to one and forgotten on the other;
#: with Station gone there is one holder of it, and the tuple stays because the
#: gates below validate against it.
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
    """Does any community this user belongs to grant ``right``?

    ``right`` must be a member of :data:`RIGHTS`; anything else raises rather
    than quietly answering False forever — a mistyped right that returns False
    is a gate nobody can pass and nobody can find.
    """
    if right not in RIGHTS:
        raise ValueError(f"{right!r} is not a privilege; see "
                         "socialhub.privileges.RIGHTS")
    person = _person_of(user)
    if person is None:
        return False
    try:
        return person.communities.functional().filter(
            **{f"privilege__{right}": True}).exists()
    except DatabaseError:
        return False
