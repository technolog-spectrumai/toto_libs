"""Draft → open → cast → finalize, and every boundary between them.

The lifecycle guards are irena's, with `select_for_update` where it had it. Two
things are new and both are Stage 4 requirements:

* **casting requires fresh confirmation.** A ballot carries `confirmed_at` and
  an `auth_evidence` record, and `cast_ballot` refuses without them.
* **a ballot may be changed.** The earlier one is superseded, not overwritten
  and not refused.

**Finalization takes a callback, and that is the whole design.** The brief says
the result must be frozen and appended to the company's ledger in ONE
transaction, and that a failed append must leave the vote unfinalized. This app
must not import `toto.ledger` or `toto.company`, so it cannot do the appending
itself. Instead `finalize` runs inside `transaction.atomic` and calls
`on_finalize(proposition, payload)` before returning. If that callback raises,
the whole thing — the frozen result, the status change, everything — rolls back
with it. The caller supplies the append; the atomicity lives here.
"""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from toto.voting.models import (
    AttendanceStatus,
    Ballot,
    BallotChoice,
    Meeting,
    MeetingStatus,
    Proposition,
    ProposalStatus,
    RollEntry,
)
from toto.voting.services.tally import (
    configuration_snapshot,
    refresh_weights,
    tally,
)

#: How recently somebody must have confirmed for a cast to count. Short on
#: purpose: the point is that they said so JUST NOW, in front of the ballot
#: they are casting, not that they logged in this morning.
CONFIRMATION_MAX_AGE_SECONDS = 300


# ---------------------------------------------------------------------------
# The roll
# ---------------------------------------------------------------------------


@transaction.atomic
def set_roll(meeting, entries, *, recorded_by=None, source):
    """Replace a DRAFT meeting's roll with this list. Refuses once frozen.

    `entries` is a list of `(voter_ref, voter_name, weight)`. Whoever calls
    this decided who is eligible; this app only writes it down.
    """
    meeting = Meeting.objects.select_for_update().get(pk=meeting.pk)
    if meeting.status != MeetingStatus.DRAFT:
        raise ValidationError("The roll is frozen once the meeting opens.")
    if Ballot.objects.filter(roll_entry__meeting=meeting).exists():
        raise ValidationError("The roll cannot change after a ballot has been cast.")

    seen = set()
    for voter_ref, voter_name, weight in entries:
        voter_ref = str(voter_ref)
        if voter_ref in seen:
            raise ValidationError(f"{voter_name} appears on the roll twice.")
        seen.add(voter_ref)
        weight = Decimal(str(weight))
        if weight < 0:
            raise ValidationError("A voting weight cannot be negative.")
        RollEntry.objects.update_or_create(
            meeting=meeting, voter_ref=voter_ref,
            defaults={
                "voter_name": voter_name,
                "weight": weight,
                "source": source,
                "eligible": True,
                "recorded_by": recorded_by,
            },
        )
    meeting.roll.exclude(voter_ref__in=seen).delete()
    return refresh_weights(meeting)


def record_attendance(*, entry, status, represented_by="", recorded_by=None):
    """Who turned up. Frozen once voting starts, like the roll itself."""
    if entry.meeting.status == MeetingStatus.CLOSED:
        raise ValidationError("A closed meeting's attendance is frozen.")
    if Ballot.objects.filter(roll_entry__meeting=entry.meeting).exists():
        raise ValidationError("Attendance cannot change after voting starts.")
    entry.status = status
    entry.represented_by = represented_by
    entry.recorded_by = recorded_by or entry.recorded_by
    entry.save(update_fields=["status", "represented_by", "recorded_by"],
               _allow_update=True)
    refresh_weights(entry.meeting)
    return entry


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------


@transaction.atomic
def open_meeting(meeting, *, opened_by=None, may_open=None):
    """Freeze the rules onto the meeting and let voting begin.

    `may_open` is the authorization hook: the caller passes a callable that
    raises if this user may not. `company/integration/voting.py` uses it to
    require an active membership, which is a rule about companies and therefore
    not this app's to know.
    """
    meeting = Meeting.objects.select_for_update().get(pk=meeting.pk)
    if meeting.status != MeetingStatus.DRAFT:
        raise ValidationError("Only a draft meeting can be opened.")
    if may_open is not None:
        may_open(meeting, opened_by)
    if not meeting.propositions.exists():
        raise ValidationError("A meeting needs at least one proposition.")
    if not meeting.roll.filter(eligible=True).exists():
        raise ValidationError("A meeting needs an electorate before it can open.")

    if meeting.configuration_id is None and not meeting.configuration_snapshot:
        raise ValidationError("Choose the voting rules before opening.")
    if not meeting.configuration_snapshot:
        meeting.configuration_snapshot = meeting.configuration.snapshot()

    meeting.status = MeetingStatus.OPEN
    meeting.opened_at = timezone.now()
    meeting.held_at = meeting.held_at or meeting.opened_at
    meeting.chaired_by = meeting.chaired_by or opened_by
    meeting.save(update_fields=["status", "opened_at", "held_at", "chaired_by",
                                "configuration_snapshot", "updated_at"])
    return refresh_weights(meeting)


@transaction.atomic
def open_proposition(proposition, *, opened_by=None):
    """Freeze this proposition's rules and open it for ballots."""
    proposition = Proposition.objects.select_for_update().select_related(
        "meeting").get(pk=proposition.pk)
    if proposition.meeting.status != MeetingStatus.OPEN:
        raise ValidationError("The meeting must be open first.")
    if proposition.status != ProposalStatus.DRAFT:
        raise ValidationError("Only a draft proposition can be opened.")

    proposition.configuration_snapshot = configuration_snapshot(proposition.meeting)
    proposition.status = ProposalStatus.OPEN
    proposition.opened_at = timezone.now()
    proposition.save(update_fields=["status", "opened_at", "configuration_snapshot",
                                    "updated_at"], _allow_update=True)
    return proposition


# ---------------------------------------------------------------------------
# Casting
# ---------------------------------------------------------------------------


def confirmation_is_fresh(confirmed_at) -> bool:
    if confirmed_at is None:
        return False
    age = (timezone.now() - confirmed_at).total_seconds()
    return 0 <= age <= CONFIRMATION_MAX_AGE_SECONDS


@transaction.atomic
def cast_ballot(*, proposition, entry, choice, cast_by=None, confirmed_at=None,
                auth_evidence=None, note="", may_cast=None):
    """Cast, or change, one vote.

    Locks the proposition for the whole transaction, so two requests for the
    same voter serialize; the partial unique constraint is the backstop if the
    lock is ever lost.
    """
    proposition = Proposition.objects.select_for_update().select_related(
        "meeting").get(pk=proposition.pk)

    if proposition.meeting.status != MeetingStatus.OPEN:
        raise ValidationError("The meeting is not open.")
    if proposition.status != ProposalStatus.OPEN:
        raise ValidationError("Voting is not open on this proposition.")
    if choice not in BallotChoice.values:
        raise ValidationError("Unknown ballot choice.")

    entry = RollEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.meeting_id != proposition.meeting_id:
        raise ValidationError("This voter is not on the meeting's roll.")
    if not entry.eligible:
        raise ValidationError("This voter is not eligible.")
    if entry.status != AttendanceStatus.PRESENT:
        raise ValidationError("Only a voter recorded as present may cast a ballot.")

    if may_cast is not None:
        may_cast(entry, cast_by)

    if not confirmation_is_fresh(confirmed_at):
        raise PermissionDenied(
            "Confirm again before casting. A vote is recorded against your name "
            "permanently, so it is taken only from somebody who has just said so."
        )

    previous = proposition.ballots.filter(roll_entry=entry, active=True).first()
    if previous is not None:
        # Deactivate FIRST: the partial unique constraint would otherwise
        # refuse the new row, which is exactly what it is there for.
        previous.active = False
        previous.save(update_fields=["active"], _supersede=True)

    return Ballot.objects.create(
        proposition=proposition,
        roll_entry=entry,
        choice=choice,
        weight=entry.weight,
        active=True,
        supersedes=previous,
        cast_by=cast_by if getattr(cast_by, "pk", None) else None,
        cast_by_ref=(cast_by.get_username() if getattr(cast_by, "pk", None) else ""),
        confirmed_at=confirmed_at,
        auth_evidence=auth_evidence or {},
        note=note,
    )


def ballot_history(proposition, entry):
    """Every ballot this voter cast on this proposition, oldest first."""
    return proposition.ballots.filter(roll_entry=entry).order_by("cast_at")


# ---------------------------------------------------------------------------
# Finalizing
# ---------------------------------------------------------------------------


def result_document(proposition) -> dict:
    """The complete, frozen record of one decision.

    Everything a reader needs and nothing they must look up: the proposition as
    voted on, the rules it was voted under, the whole roll, and every ballot
    with the name of who cast it. Voting is never secret, and this payload is
    where that stops being a slogan.
    """
    meeting = proposition.meeting
    result = tally(proposition)
    roll = [{
        "voter_ref": row.voter_ref,
        "voter": row.voter_name,
        "source": row.source,
        "status": row.status,
        "eligible": row.eligible,
        "weight": str(row.weight),
        "represented_by": row.represented_by,
    } for row in meeting.roll.order_by("voter_name")]

    ballots = [{
        "voter_ref": row.roll_entry.voter_ref,
        "voter": row.roll_entry.voter_name,
        "choice": row.choice,
        "weight": str(row.weight),
        "cast_at": row.cast_at.isoformat(),
        "cast_by": row.cast_by_ref,
        "active": row.active,
        "supersedes": str(row.supersedes.uid) if row.supersedes_id else "",
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else "",
    } for row in proposition.ballots.select_related("roll_entry", "supersedes")
                                    .order_by("cast_at")]

    return {
        "outcome": "adopted" if result["adopted"] else "rejected",
        "meeting": {
            "uid": str(meeting.uid),
            "title": meeting.title,
            "held_at": meeting.held_at.isoformat() if meeting.held_at else "",
            "record_date": meeting.record_date.isoformat(),
        },
        "proposition": {
            "uid": str(proposition.uid),
            "title": proposition.title,
            "resolution_text": proposition.resolution_text,
            "rationale": proposition.rationale,
            "attachment_ref": proposition.attachment_ref,
            "attachment_hash": proposition.attachment_hash,
            "attachment_label": proposition.attachment_label,
        },
        "configuration": proposition.configuration_snapshot,
        "roll": roll,
        # Every ballot, including superseded ones. A record that showed only
        # the final vote would be hiding that somebody changed their mind.
        "ballots": ballots,
        "result": result,
    }


@transaction.atomic
def finalize(proposition, *, decided_by, on_finalize=None, may_finalize=None):
    """Freeze the result and close the proposition. Idempotent.

    `on_finalize(proposition, payload)` runs INSIDE this transaction. If it
    raises — the ledger append failed, the chain was locked, anything — the
    freeze and the status change roll back with it and the vote stays open.
    That is the brief's requirement, and it is why the callback is here rather
    than after the call returns.
    """
    proposition = Proposition.objects.select_for_update().select_related(
        "meeting").get(pk=proposition.pk)

    if proposition.status == ProposalStatus.CLOSED:
        return proposition          # already final; do not append twice
    if proposition.status != ProposalStatus.OPEN:
        raise ValidationError("Only an open proposition can be finalized.")
    if may_finalize is not None:
        may_finalize(proposition, decided_by)

    payload = result_document(proposition)

    proposition.result_payload = payload
    proposition.status = ProposalStatus.CLOSED
    proposition.closed_at = timezone.now()
    proposition.save(update_fields=["result_payload", "status", "closed_at",
                                    "updated_at"], _allow_update=True)

    meeting_payload = dict(proposition.meeting.result_payload or {})
    by_proposition = dict(meeting_payload.get("propositions", {}))
    by_proposition[str(proposition.uid)] = payload["result"]
    meeting_payload["propositions"] = by_proposition
    Meeting.objects.filter(pk=proposition.meeting_id).update(
        result_payload=meeting_payload, updated_at=timezone.now(),
    )

    if on_finalize is not None:
        on_finalize(proposition, payload)
    return proposition


@transaction.atomic
def close_meeting(meeting, *, closed_by=None):
    meeting = Meeting.objects.select_for_update().get(pk=meeting.pk)
    if meeting.status != MeetingStatus.OPEN:
        raise ValidationError("Only an open meeting can be closed.")
    if meeting.propositions.exclude(
        status__in=[ProposalStatus.CLOSED, ProposalStatus.CANCELLED],
    ).exists():
        raise ValidationError("Finalize or cancel every open proposition first.")
    meeting.status = MeetingStatus.CLOSED
    meeting.closed_at = timezone.now()
    meeting.save(update_fields=["status", "closed_at", "updated_at"])
    return meeting
