"""The work engine: assignment → submission → review → consensus.

One module, called by BOTH product surfaces — the kanban board and any
specialised surface built on it — so that "who may review this" and "what does
three accepts mean" have exactly one answer each. A second spelling of either
is how two surfaces start disagreeing about whether a thing is done.

Nothing here is reachable from a board that does not use it. A mission with no
``ConsensusPolicy`` has no review gate, which is the state every board that
predates this module is in.

The division of labour is deliberate and worth stating once:

* ``evaluate`` is a PURE COUNT over ``Review`` rows. It writes nothing, so it
  can be called freely — on a page render, in a template, twice in a row — and
  re-running it after somebody edits a policy yields the new answer without
  touching a single recorded opinion.
* ``resolve`` is the only writer of a resolution, writes it ONCE, and is
  idempotent. Consensus never rewrites a review; it reads them and records what
  they add up to.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.people.models import Person

from .models import (
    Assignment,
    ConsensusPolicy,
    Mission,
    Practitioner,
    Review,
    ReviewVerdict,
    Submission,
    SubmissionResolution,
    SubmissionState,
    visible_missions_for,
)

#: The canonical shapes, seeded by migration and by ingress. Names are the
#: readable form because they are what an operator picks from a dropdown.
#: (name, required_reviews, required_accepts, reject_threshold, is_default)
CANONICAL_POLICIES = (
    ("1 of 1", 1, 1, 1, True),
    ("2 of 3", 3, 2, 2, False),
    ("3 of 5", 5, 3, 3, False),
)


# ── Assignment ───────────────────────────────────────────────────────────────

def open_assignment(task, person) -> Assignment:
    """Claim a task for somebody. Idempotent while the claim is live.

    ``get_or_create`` will not do: the uniqueness that matters is partial
    (one claim per person *while unreleased*), so a plain get_or_create would
    match released rows too and hand back a claim somebody already dropped.
    """
    live = Assignment.objects.filter(
        task=task, person=person, released_at__isnull=True).first()
    if live is not None:
        return live
    try:
        with transaction.atomic():
            return Assignment.objects.create(task=task, person=person)
    except IntegrityError:
        # Raced against another claim. The constraint did its job; return
        # what won rather than making a double-click an error.
        return Assignment.objects.get(
            task=task, person=person, released_at__isnull=True)


def release_assignment(task, person):
    """Drop a live claim. A timestamp, not a delete — who worked on this is
    part of the record even after they stop."""
    return Assignment.objects.filter(
        task=task, person=person, released_at__isnull=True
    ).update(released_at=timezone.now())


# ── Submission ───────────────────────────────────────────────────────────────

def start_submission(task, person, *, notes: str = "", metadata=None,
                     supersedes: Submission | None = None) -> Submission:
    """Open a DRAFT. Nothing is frozen until :func:`submit`."""
    if supersedes is not None and not supersedes.is_submitted:
        raise ValidationError(
            _("A correction supersedes a submitted submission, not a draft."))
    return Submission.objects.create(
        task=task,
        submitted_by=person,
        notes=notes,
        metadata=metadata,
        supersedes=supersedes,
    )


def submit(submission: Submission) -> Submission:
    """The one-way door.

    After this the content is frozen and a correction must be a new submission
    (see ``Submission.FROZEN_FIELDS``). Refusing a re-submit rather than making
    it a no-op is the point: a caller that submits twice believes something
    about the state that is not true, and the second call is where that shows.
    """
    if submission.state == SubmissionState.SUBMITTED:
        raise ValidationError(_("This submission has already been submitted."))
    if submission.state == SubmissionState.WITHDRAWN:
        raise ValidationError(_("A withdrawn submission cannot be re-submitted."))
    submission.state = SubmissionState.SUBMITTED
    submission.submitted_at = timezone.now()
    submission.save(update_fields=["state", "submitted_at"])
    return submission


def withdraw(submission: Submission) -> Submission:
    """Take a submission out of play without deleting what was claimed."""
    if submission.is_resolved:
        raise ValidationError(_("A resolved submission cannot be withdrawn."))
    submission.state = SubmissionState.WITHDRAWN
    submission.save(update_fields=["state"])
    return submission


def correct(submission: Submission, person, *, notes: str = "",
            metadata=None) -> Submission:
    """A correction: a NEW draft that supersedes a submitted one."""
    return start_submission(
        submission.task, person, notes=notes, metadata=metadata,
        supersedes=submission)


# ── Review ───────────────────────────────────────────────────────────────────

def can_review(user, submission: Submission) -> bool:
    """May this user review this submission?

    The one gate, so the board, the API and any other surface cannot drift.
    In order:

    1. Never your own submission. This is first and applies to staff too —
       a superuser rubber-stamping their own contribution is exactly the
       failure a review system exists to prevent.
    2. You must be able to SEE the mission, via the same
       ``visible_missions_for`` the board uses.
    3. Then: staff, or on the task's roster, or — when the roster is empty —
       a project auditor or an active reviewer on the project.
    """
    person = _person_of(user)
    if person is None:
        return False
    if submission.submitted_by_id == person.pk:
        return False

    mission_id = submission.task.mission_id
    if not visible_missions_for(user, Mission.objects.filter(pk=mission_id)).exists():
        return False

    if getattr(user, "is_staff", False) or getattr(user, "is_superuser", False):
        return True

    roster = submission.task.eligible_reviewers
    if roster.exists():
        # A named roster NARROWS: once somebody is named, project-wide
        # reviewers no longer qualify by default.
        return roster.filter(person=person).exists()

    project = submission.task.mission.campaign.project
    if project.auditors.filter(person=person).exists():
        return True
    return Practitioner.objects.filter(
        person=person,
        is_active=True,
        role=Practitioner.ROLE_REVIEWER,
        commitments__project=project,
        commitments__is_active=True,
    ).exists()


def record_review(submission: Submission, reviewer, verdict: str,
                  comment: str = "") -> Review:
    """Write one reviewer's verdict. Re-reviewing updates their own row.

    Update-not-insert because the uniqueness is (submission, reviewer): a
    reviewer who changes their mind should change THEIR verdict, not add a
    second vote for the same person.
    """
    if verdict not in ReviewVerdict.values:
        raise ValidationError(_("Unknown verdict: %(v)s") % {"v": verdict})
    if not submission.is_submitted:
        raise ValidationError(_("A draft cannot be reviewed."))
    if submission.is_resolved:
        raise ValidationError(_("This submission is already resolved."))
    review, _created = Review.objects.update_or_create(
        submission=submission,
        reviewer=reviewer,
        defaults={"verdict": verdict, "comment": comment},
    )
    return review


# ── Consensus ────────────────────────────────────────────────────────────────

def policy_for(submission: Submission) -> ConsensusPolicy | None:
    """The rule in force, or None when this mission has no review gate."""
    return submission.task.mission.effective_consensus_policy


def evaluate(submission: Submission) -> str:
    """What the reviews add up to. Reads only; writes nothing.

    Precedence is REJECT, then CHANGES, then ACCEPT, and it is deliberate: when
    a submission has simultaneously cleared the accept bar and the reject bar,
    the conservative reading is the safe one. Accepting contested material is
    the expensive mistake — it lands in whatever durable record consumes this —
    while a wrong rejection costs a correction and another round.
    """
    policy = policy_for(submission)
    if policy is None:
        return SubmissionResolution.PENDING

    counts = {ReviewVerdict.ACCEPT: 0, ReviewVerdict.REJECT: 0,
              ReviewVerdict.REQUEST_CHANGES: 0}
    for verdict in submission.reviews.values_list("verdict", flat=True):
        if verdict in counts:
            counts[verdict] += 1

    if counts[ReviewVerdict.REJECT] >= policy.reject_threshold:
        return SubmissionResolution.REJECTED
    if counts[ReviewVerdict.REQUEST_CHANGES] >= policy.effective_changes_threshold:
        return SubmissionResolution.CHANGES_REQUESTED
    if counts[ReviewVerdict.ACCEPT] >= policy.required_accepts:
        return SubmissionResolution.ACCEPTED
    return SubmissionResolution.PENDING


def resolve(submission: Submission) -> Submission:
    """Record the outcome, once.

    Idempotent by re-reading under a row lock: a submission that is already
    resolved comes straight back untouched, so a double-click, a retried task
    and a racing second reviewer all converge on one resolution rather than
    two. That matters more than it looks — Stage 2 hangs reward settlement off
    this transition, and a resolution written twice would be a reward paid
    twice.
    """
    with transaction.atomic():
        fresh = (Submission.objects
                 .select_for_update()
                 .select_related("task__mission__campaign")
                 .get(pk=submission.pk))
        if fresh.is_resolved:
            return fresh
        outcome = evaluate(fresh)
        if outcome == SubmissionResolution.PENDING:
            return fresh
        fresh.resolution = outcome
        fresh.resolved_at = timezone.now()
        fresh.save(update_fields=["resolution", "resolved_at"])
        _on_resolved(fresh)
    return fresh


def done_blocked_reason(task) -> str | None:
    """Why this task may not move to DONE, or None if nothing stops it.

    THE replacement for the single-reviewer gate. Until 1.50 a task could name
    one ``Task.reviewer`` and only that person could complete it — one opinion,
    unrecorded, and unusable the moment that person was on holiday. What
    replaces it is the consensus result: a mission that names a
    ``ConsensusPolicy`` needs a submission its reviewers actually accepted.

    A mission with no policy returns None, which is why every board that never
    opted in still moves tasks exactly as before.
    """
    policy = task.mission.effective_consensus_policy
    if policy is None:
        return None
    if task.submissions.filter(resolution=SubmissionResolution.ACCEPTED).exists():
        return None
    return _(
        "This task is reviewed under \"%(policy)s\" and needs an accepted "
        "submission before it can be completed."
    ) % {"policy": policy.name}


def _on_resolved(submission: Submission) -> None:
    """Extension point for Stage 2 (rewards). A no-op until then.

    Called INSIDE ``resolve``'s transaction so anything it records rolls back
    with the resolution; settlement defers itself to ``transaction.on_commit``
    so a ledger failure can never undo a review.

    Imported here rather than at module scope to keep ``work`` importable
    without the reward machinery, and because ``rewards`` reaches an economy
    that most hosts do not have.
    """
    from .rewards import on_submission_resolved  # noqa: PLC0415

    on_submission_resolved(submission)


# ── helpers ──────────────────────────────────────────────────────────────────

def _person_of(user):
    if not getattr(user, "is_authenticated", False):
        return None
    return Person.objects.filter(user=user).first()
