"""Casting a ballot and counting them. The only door into either.

Every consumer — the Polls pages, the Forum room, Business Center — goes
through :func:`cast`. That is the point: the rules that make a formal vote
trustworthy (closed means closed, one ballot per voter, cast once, weight
frozen) are worth nothing if three call sites each implement them, because the
third one will get it wrong and nobody will notice until a result is disputed.

The old app's voting was ``Vote.objects.update_or_create(...)`` inline in a
view, which silently made every ballot editable forever. That is fine for a
poll and disqualifying for a vote, and it is exactly the kind of decision that
has to be stated rather than inherited from whichever line of code got written
first.
"""

from __future__ import annotations

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .core import (AlreadyCast, Eligibility, NotEligible, NotOpen,
                   Result, Revisability, Tally, UnknownChoice, Visibility)
from .models import Ballot, Decision, Kind, Outcome, Question, Status


def electorate_for(question) -> object:
    """The electorate a question names, resolved through the registry.

    Resolved here rather than stored on the row, because an electorate is code
    (it asks live questions about membership and shareholdings) and the row
    stores only a key. Consumers may still pass ``electorate=`` explicitly at
    any call site; that override always wins.
    """
    from . import electorates

    return electorates.resolve(question)


def standing(question, user, *, electorate=None) -> Eligibility:
    """This user's standing, without casting anything. Never raises."""
    roll = electorate or electorate_for(question)
    try:
        return roll.standing(question, user)
    except Exception:  # noqa: BLE001 — an electorate that errors is not a crash
        return Eligibility(False, reason="Your standing could not be checked.")


@transaction.atomic
def cast(question, user, choice, *, electorate=None) -> Ballot:
    """Record one answer. Raises a VotingError subclass with a reason, or returns.

    Order matters and is the order somebody would argue about afterwards:
    is it open, are you allowed, is that a real option, have you already voted.
    """
    if not question.is_open:
        raise NotOpen("This is closed. No more answers can be recorded.")

    if choice.question_id != question.pk:
        raise UnknownChoice("That option does not belong to this question.")

    verdict = standing(question, user, electorate=electorate)
    if not verdict.allowed:
        raise NotEligible(verdict.reason or "You may not vote on this.")

    # Locked, so two clicks on a slow connection cannot become two ballots. The
    # unique constraint would catch it either way; this turns a database error
    # into a sentence.
    existing = (Ballot.objects.select_for_update()
                .filter(question=question, voter=user).first())

    if existing is None:
        return Ballot.objects.create(question=question, choice=choice,
                                     voter=user, weight=verdict.weight)

    if question.revisability == Revisability.FINAL:
        raise AlreadyCast(
            "You have already voted, and a ballot cannot be changed.")

    if existing.choice_id == choice.pk:
        return existing

    existing.choice = choice
    # The weight is NOT refreshed. It was fixed when the first ballot was cast,
    # and a revisable poll changing somebody's weight halfway through would make
    # the tally depend on when they last clicked.
    existing.revised_at = timezone.now()
    existing.revisions += 1
    existing.save(update_fields=["choice", "revised_at", "revisions"])
    return existing


def ballot_of(question, user):
    """This user's ballot, or None. Cheap enough to call on every render."""
    if not getattr(user, "is_authenticated", False):
        return None
    return Ballot.objects.filter(question=question, voter=user).first()


def may_see_results(question, user, *, electorate=None) -> bool:
    """Whether this person may read the tally yet.

    A running tally on a formal vote is an instrument for changing its outcome,
    which is why ON_CLOSE exists and why it is the default for anything called
    a vote.
    """
    if question.visibility == Visibility.LIVE:
        return True
    if question.is_open:
        return False
    if question.visibility == Visibility.ON_CLOSE:
        return True
    # ON_CLOSE_PRIVATE — closed, and only to the electorate.
    return standing(question, user, electorate=electorate).allowed


def tally(question, *, electorate=None) -> Tally:
    """Count it. Two aggregate queries regardless of how many options there are.

    Every option appears in the result, including ones nobody chose: a bar
    chart missing its empty bars misreports the shape of an opinion, and "no
    votes for this" is information.
    """
    counted = {
        row["choice_id"]: row
        for row in (question.ballots
                    .values("choice_id")
                    .annotate(weight=Sum("weight"), ballots=Count("id")))
    }

    results = []
    for choice in question.choices.all():
        row = counted.get(choice.pk)
        results.append(Result(
            choice_id=choice.pk, label=choice.label, text=choice.text,
            weight=int(row["weight"]) if row else 0,
            ballots=int(row["ballots"]) if row else 0,
        ))

    roll = electorate or electorate_for(question)
    try:
        size = int(roll.size(question) or 0)
    except Exception:  # noqa: BLE001 — a turnout is not worth an error page
        size = 0

    return Tally(
        results=tuple(results),
        total_weight=sum(r.weight for r in results),
        total_ballots=sum(r.ballots for r in results),
        electorate=size,
    )


# -- the record date ----------------------------------------------------------

def freeze_roll(question, *, electorate=None, entries=None):
    """Write the vote's register: the record date, generically.

    ``entries`` is ``[(user_or_none, label, weight), ...]`` when the caller
    (Business Center, a host) computes its own; otherwise the given data
    Electorate's members are copied. Idempotent refusal: a vote that already
    has a register keeps it — a register that can be re-frozen is a live
    register with extra steps.
    """
    from django.utils import timezone as tz

    from .electorate_models import Presence, RollEntry

    if RollEntry.objects.filter(question=question).exists():
        raise ValueError("This vote's register is already frozen.")

    if entries is None:
        if electorate is None:
            raise ValueError("freeze_roll needs an electorate or entries.")
        entries = [(member.user, member.display_name, member.weight)
                   for member in electorate.members.select_related("user")]
        if question.electorate_id != electorate.pk:
            question.electorate = electorate

    if not entries:
        raise ValueError("Nobody is on that register; there is no vote to hold.")

    # An entry is (user, label, weight), optionally followed by presence and
    # the proxy's name: attendance is taken at the same moment as the
    # register, by the caller that knows who turned up.
    normalized = []
    for entry in entries:
        user, label, weight = entry[0], entry[1], entry[2]
        presence = entry[3] if len(entry) > 3 else Presence.PRESENT
        represented_by = entry[4] if len(entry) > 4 else ""
        normalized.append((user, label, weight, presence, represented_by))
    entries = normalized

    # The pointer and the frozen marker go down BEFORE the rows: once a row
    # exists the instrument is locked, and freezing is part of opening, not
    # an edit to an open vote.
    question.metadata = {**(question.metadata or {}),
                         "roll_frozen_at": tz.now().isoformat()}
    question.save()
    RollEntry.objects.bulk_create([
        RollEntry(question=question, user=user, label=label or "",
                  weight=weight, presence=presence,
                  represented_by=represented_by or "")
        for user, label, weight, presence, represented_by in entries
    ])
    snapshot_procedure(question)
    return question


# -- exclusions (stage 9) -----------------------------------------------------

def excluded_user_ids(question) -> set:
    """Logins barred from this one vote. Cheap enough for every render."""
    from .electorate_models import VoteExclusion

    return set(VoteExclusion.objects.filter(question=question)
               .exclude(user=None).values_list("user_id", flat=True))


def is_excluded(question, user) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False
    return user.pk in excluded_user_ids(question)


def exclude_voter(question, *, user=None, label="", reason, excluded_by=None):
    """Bar one member from one vote, on the record.

    Never touches the Electorate: membership is a standing fact, exclusion is
    a fact about this question. Re-snapshots the procedure, because barring
    somebody changes what "eligible" means and the summary must not go stale.
    """
    from .electorate_models import RollEntry, VoteExclusion

    if not (reason or "").strip():
        raise ValueError("An exclusion must say why.")

    entry = None
    if user is not None:
        entry = RollEntry.objects.filter(question=question,
                                         user=user).first()
        if entry is None:
            raise ValueError(
                "That person is not on this vote's register.")
    exclusion = VoteExclusion.objects.create(
        question=question, user=user,
        label=label or (entry.label if entry else ""),
        reason=reason, excluded_by=excluded_by)
    snapshot_procedure(question)
    return exclusion


def lift_exclusion(exclusion):
    """Undo one, while the vote is still undecided. The model refuses after."""
    question = exclusion.question
    exclusion.delete()
    snapshot_procedure(question)


def snapshot_procedure(question):
    """The session's three weights, taken with the register.

    Who belongs, who is in the room, and who in the room can actually cast —
    a procedural dispute is nearly always about which of those somebody meant,
    so all three are recorded rather than derived later from rows that may
    have moved.
    """
    from .electorate_models import VoteProcedure

    rows = list(question.roll.all())
    barred = excluded_user_ids(question)
    represented = [row for row in rows if row.is_represented]
    excluded = [row for row in rows
                if row.user_id is not None and row.user_id in barred]
    procedure, _created = VoteProcedure.objects.update_or_create(
        question=question,
        defaults={
            "electorate_weight": sum(row.weight for row in rows),
            "represented_weight": sum(row.weight for row in represented),
            # Barred weight is held out: somebody excluded from this question
            # is in the room and may not vote on it.
            "eligible_weight": sum(
                row.weight for row in rows
                if row.can_act and row.user_id not in barred),
            "excluded_weight": sum(row.weight for row in excluded),
            "members_total": len(rows),
            "members_represented": len(represented),
            "members_excluded": len(excluded),
        })
    return procedure


def procedure_of(question):
    """The snapshot, or None for a vote that never froze a register."""
    from .electorate_models import VoteProcedure

    return VoteProcedure.objects.filter(question=question).first()


def snapshot_quorum(question, rule) -> None:
    """Copy a quorum rule's mode, name and number onto the vote at open."""
    question.quorum_mode = rule.mode
    question.quorum_name = rule.name
    question.quorum_threshold = rule.threshold
    question.save()


def confirm_quorum(question, user) -> None:
    """The MANUAL mode's act of authority, on the record: who, and when.

    A session fact like the notes — it happens while the meeting runs, so it
    is not caught by the instrument lock, and it freezes with the decision.
    """
    from django.utils import timezone as tz

    question.metadata = {**(question.metadata or {}), "quorum_confirmation": {
        "by": user.get_username(), "at": tz.now().isoformat()}}
    question.save()


def confirm_convening(question, aspect: str, user) -> None:
    """One of a universal meeting's three confirmations, on the record."""
    from django.utils import timezone as tz

    from .electorate_models import CONVENING_ASPECTS

    if aspect not in {key for key, _label in CONVENING_ASPECTS}:
        raise ValueError("Unknown convening confirmation.")
    confirmations = dict((question.metadata or {}).get(
        "convening_confirmations", {}))
    confirmations[aspect] = {"by": user.get_username(),
                             "at": tz.now().isoformat()}
    question.metadata = {**(question.metadata or {}),
                         "convening_confirmations": confirmations}
    question.save()


def evaluate_quorum(question, procedure) -> dict | None:
    """The snapshotted rule against the snapshotted attendance.

    None when the vote took no quorum rule. Percent and absolute bars are
    met AT the number — a quorum is "at least", where a consensus threshold
    is "strictly more".
    """
    from .electorate_models import QuorumRule

    if not question.quorum_mode:
        return None
    result = {
        "mode": question.quorum_mode,
        "name": question.quorum_name,
        "threshold": (str(question.quorum_threshold)
                      if question.quorum_threshold is not None else None),
        "represented_weight": procedure.represented_weight if procedure else 0,
        "electorate_weight": procedure.electorate_weight if procedure else 0,
        "attendance_percent": (procedure.attendance_percent
                               if procedure else 0.0),
    }
    if question.quorum_mode == QuorumRule.Mode.NONE:
        result["met"] = True
    elif question.quorum_mode == QuorumRule.Mode.PERCENT:
        result["met"] = (procedure is not None
                         and procedure.attendance_percent
                         >= float(question.quorum_threshold or 0))
    elif question.quorum_mode == QuorumRule.Mode.ABSOLUTE:
        result["met"] = (procedure is not None
                         and procedure.represented_weight
                         >= float(question.quorum_threshold or 0))
    elif question.quorum_mode == QuorumRule.Mode.MANUAL:
        confirmation = (question.metadata or {}).get("quorum_confirmation")
        result["met"] = bool(confirmation)
        result["confirmation"] = confirmation
    else:
        result["met"] = False
    return result


def convening_state(question) -> dict:
    """The convening mode and whatever has been confirmed so far."""
    from .electorate_models import CONVENING_ASPECTS, ConveningMode

    confirmations = (question.metadata or {}).get(
        "convening_confirmations", {})
    mode = question.convening_mode or ConveningMode.FORMAL
    return {
        "mode": mode,
        "confirmations": confirmations,
        # Only a universal meeting owes confirmations; a formally convened
        # one owes nothing, and listing three "missing" facts it never
        # needed would misread as a defect.
        "missing": ([key for key, _label in CONVENING_ASPECTS
                     if key not in confirmations]
                    if mode == ConveningMode.UNIVERSAL else []),
    }


def snapshot_rule(question, profile) -> None:
    """Copy a consensus profile's name AND number onto the vote — so a later
    retune of the profile changes future votes only."""
    question.rule_name = profile.name
    question.rule_percent = profile.percent
    question.save()


def _signed_sides(question, count):
    """(for_weight, against_weight, denominator) under the choice-value sign
    convention; falls back to winner-vs-rest when no choice is signed."""
    values = {c.pk: c.value for c in question.choices.all()}
    signed = any(v != 0 for v in values.values())
    if signed:
        for_weight = sum(r.weight for r in count.results
                         if values.get(r.choice_id, 0) > 0)
        against = sum(r.weight for r in count.results
                      if values.get(r.choice_id, 0) < 0)
        return for_weight, against, for_weight + against
    winner = count.winner
    for_weight = winner.weight if winner else 0
    return for_weight, count.total_weight - for_weight, count.total_weight


def evaluate_consensus(question, count) -> dict | None:
    """The snapshotted rule against the count. None when no rule was taken."""
    if question.rule_percent is None:
        return None
    for_weight, against, denominator = _signed_sides(question, count)
    achieved = (for_weight / denominator * 100) if denominator else 0.0
    threshold = float(question.rule_percent)
    return {
        "name": question.rule_name,
        "percent": str(question.rule_percent),
        "for_weight": for_weight,
        "against_weight": against,
        "denominator_weight": denominator,
        "achieved_percent": round(achieved, 2),
        # Strictly above — the same comparison the company constitution has
        # always made. Nothing decided (denominator 0) is not adoption.
        "adopted": bool(denominator) and achieved > threshold,
    }


def outcome_fixed(question, count=None) -> bool:
    """May this vote finalize early? Only when the ballots still out cannot
    move the adopted/rejected answer, whichever way they all fall.

    Meaningful only for votes that snapshotted a rule and froze a register:
    without a rule there is no outcome to fix, and without a register there
    is no bound on what remains.
    """
    from .electorate_models import RollEntry

    if question.rule_percent is None:
        return False
    entries = RollEntry.objects.filter(question=question)
    if not entries.exists():
        return False

    count = count or tally(question, electorate=electorate_for(question))
    voted = set(question.ballots.values_list("voter_id", flat=True))
    barred = excluded_user_ids(question)
    # Weight that is barred can never arrive, so it does not keep an
    # outcome "open" — a vote whose only holdouts are excluded is settled.
    remaining = sum(e.weight for e in entries
                    if e.user_id is not None and e.user_id not in voted
                    and e.user_id not in barred)

    for_weight, against, denominator = _signed_sides(question, count)
    threshold = float(question.rule_percent)
    full = denominator + remaining
    if full == 0:
        return True
    # Worst case for adoption: every outstanding ballot lands against.
    certainly_adopted = (for_weight / full * 100) > threshold
    # Best case: every outstanding ballot lands for — and it still fails.
    certainly_rejected = ((for_weight + remaining) / full * 100) <= threshold
    return certainly_adopted or certainly_rejected


# -- recording a decision -----------------------------------------------------

def _assert_canonical(content, path="content"):
    """Refuse a decision snapshot whose serialization could drift.

    ``compute_hash`` re-serializes ``content`` from the stored object on
    every verify with ``default=str`` — so a datetime or Decimal that slips
    in hashes as its str() TODAY and as whatever the JSONField round-trip
    returns TOMORROW, and the chain would report tampering that never
    happened. Only the JSON-native leaves survive a round-trip bit-identical;
    everything else must be converted by the caller, deliberately, before
    the row is hashed. Raising here turns a future silent chain break into a
    loud test failure at write time.
    """
    if isinstance(content, dict):
        for key, value in content.items():
            if not isinstance(key, str):
                raise ValueError(
                    f"{path}: non-string key {key!r} would not survive a "
                    "JSON round-trip identically.")
            _assert_canonical(value, f"{path}.{key}")
    elif isinstance(content, (list, tuple)):
        for index, value in enumerate(content):
            _assert_canonical(value, f"{path}[{index}]")
    elif not (content is None or isinstance(content, (str, int, float, bool))):
        raise ValueError(
            f"{path}: {type(content).__name__} is not JSON-native; convert "
            "it before the snapshot is hashed, or the chain will break when "
            "the stored copy re-serializes differently.")


def record_decision(question, *, decided_by=None, when=None) -> Decision:
    """Close a formal vote and write its result down, once.

    The Tally is recomputed on every page view; the Decision is the one copy
    that is not — "what was decided" has to survive later edits and deletions.
    Idempotent: recording an already-decided vote returns the existing row.

    Policy: after the deadline the clock decides and anyone (or nothing —
    ``decided_by=None``) may trigger the recording. BEFORE the deadline, or
    when there is no deadline at all, closing is an act of authority: only
    staff may do it, and the row says who.
    """
    if question.kind != Kind.VOTE:
        raise ValueError("Only a formal vote is recorded. A poll just closes.")

    with transaction.atomic():
        question = (Question.objects.select_for_update()
                    .get(pk=question.pk))
        existing = Decision.objects.filter(question=question).first()
        if existing is not None:
            return existing

        now = timezone.now()
        deadline_passed = (question.closes_at is not None
                           and question.closes_at <= now)
        if not deadline_passed:
            if decided_by is None or not (decided_by.is_staff
                                          or decided_by.is_superuser):
                raise PermissionDenied(
                    "Closing a vote before its deadline is a staff act.")
            # A vote that took a consensus rule may finalize early ONLY once
            # the outstanding ballots cannot move the answer — authority does
            # not get to call a race that is still running.
            if question.rule_percent is not None and not outcome_fixed(question):
                raise PermissionDenied(
                    "The outcome could still change; this vote cannot be "
                    "finalized early.")

        from . import electorates, governance

        roll = electorate_for(question)
        count = tally(question, electorate=roll)
        consensus = evaluate_consensus(question, count)
        procedure = procedure_of(question)
        quorum = evaluate_quorum(question, procedure)
        convening = convening_state(question)

        from .electorate_models import ConveningMode

        if question.convening_mode == ConveningMode.UNIVERSAL:
            # A universal meeting IS its three confirmations plus the fact of
            # everybody being in the room; without them there was no meeting
            # entitled to decide, and recording would launder that.
            if convening["missing"]:
                raise PermissionDenied(
                    "A universal meeting needs all three confirmations "
                    "before its decision can be recorded.")
            if procedure is None or procedure.attendance_percent < 100.0:
                raise PermissionDenied(
                    "A universal meeting requires the entire voting weight "
                    "to be represented; the attendance snapshot says it "
                    "is not.")

        if consensus is not None and quorum is not None and not quorum["met"]:
            # A threshold vote in a session without quorum cannot adopt —
            # the arithmetic stays in the record, the verdict does not
            # survive the missing room.
            consensus = {**consensus, "adopted": False,
                         "quorum_blocked": True}

        # What the scope's own rules make of that count. None when the scope
        # has no rule, which is every scope the engine ships: "passed" is a
        # constitution, not arithmetic. Not caught — a judge that raises is a
        # bug in the rule, and this transaction rolls the whole recording back
        # rather than writing a decision that cannot be corrected.
        verdict = governance.judge(question, count)

        if count.total_ballots == 0:
            outcome, winner_label, winner_ref = Outcome.NO_BALLOTS, "", None
        elif count.winner is None:
            outcome, winner_label, winner_ref = Outcome.TIE, "", None
        else:
            outcome = Outcome.WINNER
            winner_label = count.winner.label
            winner_ref = {"choice_id": count.winner.choice_id,
                          "label": count.winner.label}

        # An overdue vote closed AT its deadline, whenever the recording ran;
        # an early close closed when the closer said so.
        closed_when = when or (question.closes_at if deadline_passed else now)
        question.close(when=closed_when)

        proposer = question.created_by
        ballots = [{
            "voter_id": b.voter_id,
            "voter_username": b.voter.get_username(),
            "choice_label": b.choice.label,
            "weight": b.weight,
            "cast_at": b.cast_at.isoformat(),
        } for b in question.ballots.select_related("voter", "choice")]

        content = {
            "question": {
                "pk": question.pk, "kind": question.kind,
                "title": question.title,
                "question_text": question.question_text,
                "body": question.body, "slug": question.slug,
                "scope_type": question.scope_type,
                "scope_id": question.scope_id,
            },
            "proposer": ({"id": proposer.pk,
                          "username": proposer.get_username()}
                         if proposer else None),
            "window": {
                "opens_at": question.opens_at.isoformat(),
                "closes_at": (question.closes_at.isoformat()
                              if question.closes_at else None),
                "closed_at": (question.closed_at.isoformat()
                              if question.closed_at else None),
            },
            "decision_header": question.decision_header,
            "decision_comment": question.decision_comment,
            "electorate": {"key": electorates.key_of(question),
                           "name": (question.electorate.name
                                    if question.electorate_id else ""),
                           "size": count.electorate},
            "roll": [{
                "user_id": entry.user_id,
                "label": entry.label or (entry.user.get_username()
                                         if entry.user_id else ""),
                "weight": entry.weight,
                "presence": entry.presence,
                "represented_by": entry.represented_by,
            } for entry in question.roll.all()],
            "procedure": (procedure.as_dict(notes=question.procedural_notes)
                          if procedure else None),
            "quorum": quorum,
            "convening": convening,
            "exclusions": [{
                "user_id": exclusion.user_id,
                "label": exclusion.label,
                "reason": exclusion.reason,
                "excluded_by": (exclusion.excluded_by.get_username()
                                if exclusion.excluded_by_id else None),
                "created_at": exclusion.created_at.isoformat(),
            } for exclusion in question.exclusions.select_related(
                "excluded_by")],
            "consensus": consensus,
            "outcome": {"outcome": outcome, "winner": winner_ref},
            "tally": [{
                "choice_id": r.choice_id, "label": r.label,
                "weight": r.weight, "ballots": r.ballots,
            } for r in count.results],
            "totals": {"weight": count.total_weight,
                       "ballots": count.total_ballots,
                       "turnout": count.turnout},
            "ballots": ballots,
            # Always present, null where the scope has no rule — so a reader
            # can tell "nobody judges this" from "the judge said nothing".
            "governance": verdict,
        }

        _assert_canonical(content)
        try:
            return Decision.objects.create(
                question=question,
                scope_type=question.scope_type, scope_id=question.scope_id,
                title=question.title,
                outcome=outcome, winner_label=winner_label,
                electorate_key=electorates.key_of(question),
                electorate_size=count.electorate,
                total_ballots=count.total_ballots,
                total_weight=count.total_weight,
                turnout=count.turnout,
                adopted=consensus["adopted"] if consensus else None,
                decided_by=decided_by,
                content=content,
            )
        except IntegrityError:
            # Two recorders raced past the filter; the OneToOne held. The
            # other one's row IS the decision.
            return Decision.objects.get(question=question)


def filtered_decisions(params, *, scope_type: str = "", scope_id: str = ""):
    """The ledger queryset plus the filters that shaped it.

    One place, because a ledger PAGE and its PDF export must agree exactly —
    and now also because a scope owner (Business Center) renders the same
    ledger for one company. Takes a plain mapping rather than a request, so a
    host app can call it without importing the view layer.

    The global scope is the DEFAULT, not a constant buried in here: the polls
    views call this with no scope arguments and stay locked to SCOPE_GLOBAL,
    while a scoped call site is a different view behind a different gate in
    the app that owns that scope. No polls URL can reach another scope.
    """
    decisions = Decision.objects.in_scope(scope_type, scope_id)

    outcome = params.get("outcome") or ""
    if outcome in Outcome.values:
        decisions = decisions.filter(outcome=outcome)

    filters = {"outcome": outcome}
    for param, lookup in (("from", "decided_at__date__gte"),
                          ("to", "decided_at__date__lte")):
        raw = params.get(param) or ""
        filters[param] = raw
        if raw:
            try:
                decisions = decisions.filter(**{lookup: raw})
            except Exception:  # noqa: BLE001 — a malformed date is not an error page
                filters[param] = ""

    return decisions.select_related("question", "decided_by"), filters


def record_overdue(scope_type: str = "", scope_id: str = "") -> int:
    """Record every formal vote in one scope whose deadline has passed.

    Called from the pages that could display a decided vote — the tab, the
    ledger, the results page — so the decision exists by the time anything
    could show it. Deterministic: after the deadline no ballot can change
    (cast() checks the clock), so it does not matter which request runs it.
    Bounded: one indexed query, usually empty.
    """
    overdue = (Question.objects.in_scope(scope_type, scope_id)
               .filter(kind=Kind.VOTE, status=Status.OPEN,
                       closes_at__lte=timezone.now(),
                       decision__isnull=True))
    recorded = 0
    for question in overdue:
        record_decision(question)
        recorded += 1
    return recorded
