"""The two writes Hesperis owns: accepting an observation, and freezing a release.

Both are single-writer on purpose. Every other way to create an
``AcceptedObservation`` or a ``DatasetVersionMember`` is a way to get a fact
into a dataset without it having survived review, which is the failure this
whole app is shaped to prevent.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from toto.kanban import work
from toto.kanban.models import SubmissionResolution, Task

from .models import (
    AcceptedObservation,
    DatasetVersion,
    DatasetVersionMember,
    HesperisBounty,
)


# ── contributing ─────────────────────────────────────────────────────────────

def contribute(bounty: HesperisBounty, person, *, notes: str = "",
               metadata=None):
    """Open this person's own slot under the bounty, and start a draft.

    THE reason a bounty can take many contributions without a second task
    system: each contributor gets their own kanban Task under the bounty's
    mission, and everything downstream — assignment, submission, review,
    consensus, reward, metrics, permissions — is ordinary kanban from there.

    Idempotent in the useful sense: a contributor who already has an open draft
    gets it back rather than accumulating empty ones.
    """
    if not bounty.is_open():
        raise ValidationError(_("This bounty is not accepting contributions."))

    task = (Task.objects
            .filter(mission_id=bounty.mission_id,
                    assignments__person=person,
                    assignments__released_at__isnull=True)
            .first())
    if task is None:
        task = Task.objects.create(
            mission=bounty.mission,
            title=f"{bounty.mission.title} — {person}",
        )
    work.open_assignment(task, person)

    existing = task.submissions.filter(
        submitted_by=person, state="draft").first()
    if existing is not None:
        return existing
    return work.start_submission(
        task, person, notes=notes, metadata=metadata)


# ── accepting ────────────────────────────────────────────────────────────────

def accept(submission, *, observed_at=None, location=None, zone=None,
           payload=None) -> AcceptedObservation:
    """Turn an accepted submission into a durable observation.

    THE ONLY WRITER of ``AcceptedObservation``. It refuses anything whose
    resolution did not come from consensus, which is what makes "an observation
    exists only because reviewers accepted it" a property of the system rather
    than a convention people follow.

    Two conditions, and both matter:

    * ``resolution == ACCEPTED`` — the outcome.
    * ``resolved_at`` is set — that the outcome was written by
      ``work.resolve`` rather than poked into the column. A row with an
      accepted resolution and no timestamp cannot exist anyway (there is a
      CheckConstraint), which is precisely why checking it here is cheap and
      worth doing.

    Idempotent: the OneToOne means one submission can produce at most one
    observation, and calling twice returns the first rather than raising.
    """
    if submission.resolution != SubmissionResolution.ACCEPTED:
        raise ValidationError(
            _("Only an accepted submission becomes an observation (this one is "
              "%(state)s).") % {"state": submission.get_resolution_display()})
    if submission.resolved_at is None:
        raise ValidationError(
            _("This submission's acceptance did not come from consensus."))

    existing = AcceptedObservation.objects.filter(submission=submission).first()
    if existing is not None:
        return existing

    bounty = getattr(submission.task.mission, "hesperis_bounty", None)
    if bounty is None:
        raise ValidationError(
            _("This submission is not against a Hesperis bounty."))

    return AcceptedObservation.objects.create(
        submission=submission,
        bounty=bounty,
        observed_by=submission.submitted_by,
        observed_at=observed_at or submission.submitted_at,
        location=location,
        zone=zone,
        payload=payload if payload is not None else submission.metadata,
    )


def accept_all_pending(bounty: HesperisBounty) -> list[AcceptedObservation]:
    """Sweep: every accepted submission under this bounty that has no
    observation yet. Safe to re-run — ``accept`` is idempotent."""
    from toto.kanban.models import Submission

    made = []
    pending = Submission.objects.filter(
        task__mission_id=bounty.mission_id,
        resolution=SubmissionResolution.ACCEPTED,
        accepted_observation__isnull=True,
    )
    for submission in pending:
        made.append(accept(submission))
    return made


# ── freezing ─────────────────────────────────────────────────────────────────

def freeze(dataset, *, by=None, notes: str = "") -> DatasetVersion:
    """Publish an immutable release of everything accepted so far.

    Membership is COPIED into rows, not queried later. That is the whole
    difference between a release and a view: this version keeps meaning what it
    meant even after the campaign accepts a thousand more observations.

    The manifest hash is written LAST, and ``DatasetVersionMember.save``
    refuses to add to a version that already has one — so the hash doubles as
    the "frozen" flag rather than needing a separate boolean that could
    disagree with it.
    """
    observations = list(
        AcceptedObservation.objects
        .filter(bounty__mission__campaign_id=dataset.campaign.campaign_id)
        .order_by("uid"))

    with transaction.atomic():
        last = (DatasetVersion.objects
                .filter(dataset=dataset)
                .order_by("-number")
                .first())
        version = DatasetVersion.objects.create(
            dataset=dataset,
            number=(last.number + 1) if last else 1,
            notes=notes,
            frozen_by=by,
            frozen_at=timezone.now(),
        )
        DatasetVersionMember.objects.bulk_create([
            DatasetVersionMember(version=version, observation=observation)
            for observation in observations
        ])
        version.manifest_hash = version.compute_manifest_hash()
        version.save(update_fields=["manifest_hash"])
    return version
