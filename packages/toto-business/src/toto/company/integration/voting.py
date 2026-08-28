"""The seam between a Company and a vote.

`toto.voting` knows nothing about companies: it holds a frozen roll of soft
voter references and calls back for anything that needs judgement. This module
supplies all three of those judgements —

* **who may act** — creating or opening a vote requires an active membership;
* **who may vote** — shareholders, members, or hand-picked people;
* **what happens at finalization** — the result is appended to this Company's
  chain, inside voting's own transaction.

and it is the only module in the tree that knows a vote's `scope_type` string
means a Company.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import PermissionDenied

from toto.company.models import Company, CompanyMembership, ShareHolding
from toto.voting.models import Meeting, RollSource
from toto.voting.services import lifecycle

#: How a vote names a Company. A string, never an import.
SCOPE_TYPE = "company.company"


def meetings_for(company: Company):
    return Meeting.objects.filter(scope_type=SCOPE_TYPE, scope_uid=company.uid)


def company_for(meeting: Meeting):
    if meeting.scope_type != SCOPE_TYPE or not meeting.scope_uid:
        return None
    return Company.objects.filter(uid=meeting.scope_uid).first()


# ---------------------------------------------------------------------------
# Who may act
# ---------------------------------------------------------------------------


def is_member(company: Company, user) -> bool:
    """An ACTIVE membership, linked through people.Person to this user."""
    if user is None or not getattr(user, "pk", None):
        return False
    return CompanyMembership.objects.filter(
        company=company, active=True, person__user=user,
    ).exists()


def require_membership(company: Company, user, *, action="do this"):
    """Membership is the qualification. Staff are not exempt.

    A deliberate choice, and the same rule the platform settled on when
    citizenship was retired: belonging is what entitles you, not a flag on your
    account. An administrator who is not a member of this company has no
    business opening its votes — they can add themselves as a member first,
    which is a visible act rather than an invisible privilege.
    """
    if not is_member(company, user):
        raise PermissionDenied(
            f"Only an active member of {company.name} may {action}."
        )


def may_open(meeting, user):
    company = company_for(meeting)
    if company is None:
        raise PermissionDenied("This vote belongs to no company that still exists.")
    require_membership(company, user, action="open a vote")


def may_finalize(proposition, user):
    company = company_for(proposition.meeting)
    if company is None:
        raise PermissionDenied("This vote belongs to no company that still exists.")
    require_membership(company, user, action="finalize a vote")


def may_cast(entry, user):
    """Casting is bound to the voter, not merely to membership.

    A member may not cast somebody else's ballot just by being a member. The
    roll entry names who this vote belongs to, and only that person — or
    somebody they have a recorded representation from — may use it.
    """
    company = company_for(entry.meeting)
    if company is None:
        raise PermissionDenied("This vote belongs to no company that still exists.")
    if user is None or not getattr(user, "pk", None):
        raise PermissionDenied("Sign in to vote.")

    if entry.voter_ref == user_ref(user):
        return
    if entry.represented_by and entry.represented_by == user_ref(user):
        return
    raise PermissionDenied(
        f"This ballot belongs to {entry.voter_name}. You may cast it only as "
        "their recorded representative."
    )


def user_ref(user) -> str:
    """How a user appears on a roll. Stable, and not a database id."""
    person = getattr(user, "community_profile", None)
    if person is not None:
        return f"person:{person.uid}"
    return f"user:{user.pk}"


# ---------------------------------------------------------------------------
# Who may vote
# ---------------------------------------------------------------------------


def shareholder_roll(company: Company, *, as_of=None):
    """Voting rights read off the share register at the record date.

    Weight is VOTES, not units: a share class may carry a `votes_per_unit`
    other than one, and control is what a vote is about.
    """
    from django.db.models import Q
    from django.utils import timezone

    as_of = as_of or timezone.localdate()
    holdings = (
        ShareHolding.objects
        .filter(share_class__company=company, since__lte=as_of)
        .filter(Q(until__isnull=True) | Q(until__gte=as_of))
        .select_related("party", "party__person", "share_class")
    )
    weights = {}
    names = {}
    for holding in holdings:
        key = f"party:{holding.party.uid}"
        weights[key] = weights.get(key, Decimal("0")) + holding.votes
        names[key] = holding.party.name
    return [
        (ref, names[ref], weight)
        for ref, weight in sorted(weights.items(), key=lambda item: names[item[0]].lower())
        if weight > 0
    ]


def member_roll(company: Company):
    """One member, one vote. The other shape a company votes in."""
    return [
        (f"person:{membership.person.uid}", membership.person.display_name, Decimal("1"))
        for membership in company.memberships.filter(active=True)
        .select_related("person").order_by("person__display_name")
    ]


def selected_roll(people, *, weight=Decimal("1")):
    """Hand-picked existing people, one vote each unless told otherwise."""
    return [
        (f"person:{person.uid}", person.display_name, Decimal(str(weight)))
        for person in people
    ]


ROLL_BUILDERS = {
    RollSource.SHAREHOLDERS: shareholder_roll,
    RollSource.MEMBERS: member_roll,
}


def set_electorate(meeting, *, source, recorded_by=None, people=None):
    """Freeze one of the three electorates onto a draft meeting."""
    company = company_for(meeting)
    if company is None:
        raise PermissionDenied("This vote belongs to no company that still exists.")
    require_membership(company, recorded_by, action="set an electorate")

    if source == RollSource.SHAREHOLDERS:
        entries = shareholder_roll(company, as_of=meeting.record_date)
    elif source == RollSource.MEMBERS:
        entries = member_roll(company)
    elif source == RollSource.SELECTED:
        entries = selected_roll(people or [])
    else:
        raise ValueError(f"Unknown electorate source {source!r}.")

    return lifecycle.set_roll(meeting, entries, recorded_by=recorded_by, source=source)


# ---------------------------------------------------------------------------
# Finalization → the chain
# ---------------------------------------------------------------------------


def append_decision(proposition, payload, *, actor=None):
    """Append one finalized decision to its Company's chain.

    Called from INSIDE `lifecycle.finalize`'s transaction. If this raises, the
    freeze and the status change roll back with it and the vote stays open —
    which is the brief's requirement, and the reason this is a callback rather
    than something the caller does afterwards.
    """
    from toto.company.integration import ledger as bc_ledger
    from toto.ledger.services import chain

    company = company_for(proposition.meeting)
    if company is None:
        raise PermissionDenied("This vote belongs to no company that still exists.")

    entry = chain.append(
        ledger=bc_ledger.company_ledger(company, actor=actor),
        payload={"decision": payload, "company": bc_ledger.company_facts(company)},
        actor=actor,
        source_type="voting.proposition",
        source_uid=proposition.uid,
        source_ref=proposition.title,
    )
    # Written through the same _allow_update door finalize() used; the row is
    # already CLOSED by now, and this is the block it became.
    type(proposition).objects.filter(pk=proposition.pk).update(block_uid=entry.uid)
    proposition.block_uid = entry.uid
    return entry


def finalize_proposition(proposition, *, decided_by):
    """Finalize and append, atomically. The Stage 4 headline."""
    return lifecycle.finalize(
        proposition,
        decided_by=decided_by,
        may_finalize=may_finalize,
        on_finalize=lambda prop, payload: append_decision(
            prop, payload, actor=decided_by,
        ),
    )


def open_company_meeting(meeting, *, opened_by):
    return lifecycle.open_meeting(meeting, opened_by=opened_by, may_open=may_open)


def cast(*, proposition, entry, choice, cast_by, confirmed_at=None,
         auth_evidence=None, note=""):
    return lifecycle.cast_ballot(
        proposition=proposition, entry=entry, choice=choice, cast_by=cast_by,
        confirmed_at=confirmed_at, auth_evidence=auth_evidence, note=note,
        may_cast=may_cast,
    )
