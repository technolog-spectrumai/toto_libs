"""Counting. The arithmetic is irena's, unchanged, and it is deliberate.

Every branch here was written against a real rulebook: quorum on weight or on
heads, a majority measured against ballots cast / decisive votes / present
weight / all eligible weight, abstentions included, excluded or counted
against, and a threshold that must be exceeded or may be met. Those combinations
are not hypothetical — they are what different companies' articles actually
say — so the tests walk the matrix rather than the happy path.

Two changes, both consequences of Stage 4:

* only **active** ballots count, because a voter may now change their mind;
* the roll is soft, so weights come from `RollEntry` rather than a share
  register this app would have to understand.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from toto.voting.models import (
    AbstentionRule,
    AttendanceStatus,
    BallotChoice,
    MajorityBasis,
    QuorumBasis,
    ThresholdMode,
)


def configuration_snapshot(meeting, proposition=None) -> dict:
    """The rules this count runs under. The frozen copy, never the live row.

    Proposition first, then meeting, then the configuration itself — so a vote
    opened last week is counted under last week's rules even if staff edited
    the configuration since.
    """
    if proposition is not None and proposition.configuration_snapshot:
        return dict(proposition.configuration_snapshot)
    if meeting.configuration_snapshot:
        return dict(meeting.configuration_snapshot)
    if meeting.configuration_id:
        return meeting.configuration.snapshot()
    return {
        "quorum_basis": QuorumBasis.WEIGHT,
        "quorum_percent": "50",
        "majority_basis": MajorityBasis.CAST,
        "majority_percent": "50",
        "abstention_rule": AbstentionRule.INCLUDE,
        "threshold_mode": ThresholdMode.STRICT,
    }


def _threshold_met(achieved, required, mode):
    if mode == ThresholdMode.INCLUSIVE:
        return achieved >= required
    return achieved > required


def refresh_weights(meeting):
    """Recompute what the roll adds up to, and whether quorum is met."""
    eligible = Decimal("0")
    present = Decimal("0")
    eligible_voters = 0
    present_voters = 0
    for row in meeting.roll.all():
        if not row.eligible:
            continue
        eligible += row.weight
        eligible_voters += 1
        if row.status == AttendanceStatus.PRESENT:
            present += row.weight
            present_voters += 1

    rules = configuration_snapshot(meeting)
    required = Decimal(str(rules.get("quorum_percent", "50")))
    basis = rules.get("quorum_basis", QuorumBasis.WEIGHT)
    if basis == QuorumBasis.MEMBERS:
        achieved = (Decimal(present_voters) / Decimal(eligible_voters) * 100
                    if eligible_voters else Decimal("0"))
    else:
        achieved = present / eligible * 100 if eligible else Decimal("0")
    quorum_met = _threshold_met(
        achieved, required, rules.get("threshold_mode", ThresholdMode.STRICT),
    )
    # A quorum of zero is met by an empty room; anything above it is not.
    if not eligible:
        quorum_met = required == 0

    type(meeting).objects.filter(pk=meeting.pk).update(
        eligible_weight=eligible, present_weight=present,
        eligible_voters=eligible_voters, present_voters=present_voters,
        quorum_met=quorum_met,
    )
    meeting.eligible_weight = eligible
    meeting.present_weight = present
    meeting.eligible_voters = eligible_voters
    meeting.present_voters = present_voters
    meeting.quorum_met = quorum_met
    return meeting


def active_ballots(proposition):
    """What counts. Superseded ballots are history, not votes."""
    return proposition.ballots.filter(active=True).select_related("roll_entry")


def tally(proposition) -> dict:
    """Count one proposition and return the complete, frozen result.

    Deterministic: same ballots and same rules, same payload, every time. The
    figures are strings rather than Decimals so the payload can be hashed and
    compared without a float ever entering the picture.
    """
    totals = defaultdict(lambda: Decimal("0"))
    ballot_rows = []
    for ballot in active_ballots(proposition).order_by("cast_at"):
        totals[ballot.choice] += ballot.weight
        ballot_rows.append(ballot)

    meeting = proposition.meeting
    refresh_weights(meeting)
    rules = configuration_snapshot(meeting, proposition)

    for_weight = totals[BallotChoice.FOR]
    against_weight = totals[BallotChoice.AGAINST]
    abstain_weight = totals[BallotChoice.ABSTAIN]
    abstention_rule = rules.get("abstention_rule", AbstentionRule.INCLUDE)
    counted_against = against_weight + (
        abstain_weight if abstention_rule == AbstentionRule.AGAINST else Decimal("0")
    )

    basis = rules.get("majority_basis", MajorityBasis.CAST)
    if basis == MajorityBasis.ELIGIBLE:
        denominator = meeting.eligible_weight
    elif basis == MajorityBasis.PRESENT:
        denominator = meeting.present_weight
    elif basis == MajorityBasis.DECISIVE:
        denominator = for_weight + counted_against
    else:
        denominator = for_weight + against_weight
        if abstention_rule != AbstentionRule.EXCLUDE:
            denominator += abstain_weight

    achieved = for_weight / denominator * Decimal("100") if denominator else Decimal("0")
    required = Decimal(str(rules.get("majority_percent", "50")))
    majority_met = bool(denominator) and _threshold_met(
        achieved, required, rules.get("threshold_mode", ThresholdMode.STRICT),
    )

    return {
        "for_weight": str(for_weight),
        "against_weight": str(against_weight),
        "abstain_weight": str(abstain_weight),
        "counted_against_weight": str(counted_against),
        "denominator_weight": str(denominator),
        "majority_percent": str(required),
        "achieved_percent": str(achieved.quantize(Decimal("0.01"))),
        "majority_met": majority_met,
        "eligible_weight": str(meeting.eligible_weight),
        "present_weight": str(meeting.present_weight),
        "eligible_voters": meeting.eligible_voters,
        "present_voters": meeting.present_voters,
        "quorum_met": meeting.quorum_met,
        "adopted": bool(meeting.quorum_met and majority_met),
        "ballots_counted": len(ballot_rows),
        "ballots_cast_total": proposition.ballots.count(),
        "rules": rules,
    }
