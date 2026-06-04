from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import (
    Responder,
    MobilizationReport,
    MobilizationReportEvidence,
    MobilizationEvent,
)
from toto.response.models import (
    Deployment,
    DeploymentAssignment,
    Intervention,
)

_SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_WEIGHT_TO_SEVERITY = {
    "low": "low",
    "normal": "medium",
    "high": "high",
}
_ROLE_SEVERITY_BUMP = {
    "primary": 1,
    "supporting": 0,
    "context": 0,
    "contradictory": -1,
}


def can_enact_report(person, report):
    """Return True if person is the community head or a senior member."""
    community = report.community
    if community.head_id and community.head_id == person.pk:
        return True
    return community.senior_members.filter(pk=person.pk).exists()


def create_report_from_incident(community, incident, submitted_by, **kwargs):
    """Create a MobilizationReport pre-linked to an incident as primary evidence."""
    report = MobilizationReport.objects.create(
        community=community,
        submitted_by=submitted_by,
        severity=incident.severity if hasattr(incident, "severity") else "low",
        **kwargs,
    )
    MobilizationReportEvidence.objects.create(
        report=report,
        incident=incident,
        evidence_role="primary",
        weight="high",
        added_by=submitted_by,
    )
    return report


def add_incident_evidence(report, incident, added_by=None, **kwargs):
    """Add an incident as evidence to a report; update severity afterward."""
    evidence, created = MobilizationReportEvidence.objects.get_or_create(
        report=report,
        incident=incident,
        defaults={"added_by": added_by, **kwargs},
    )
    if not created:
        for attr, value in kwargs.items():
            setattr(evidence, attr, value)
        evidence.save()
    update_report_severity_from_evidence(report)
    return evidence


def update_report_severity_from_evidence(report):
    """Recalculate report severity from attached incidents."""
    evidence_qs = report.evidence_links.select_related("incident").all()
    if not evidence_qs.exists():
        return

    score = 0
    for ev in evidence_qs:
        incident = ev.incident
        det_severity = getattr(incident, "severity", "low")
        base = _SEVERITY_ORDER.get(det_severity, 0)
        bump = _ROLE_SEVERITY_BUMP.get(ev.evidence_role, 0)
        weight_factor = {"low": 0.5, "normal": 1.0, "high": 1.5}.get(ev.weight, 1.0)
        score += (base + bump) * weight_factor

    count = evidence_qs.count()
    avg = score / count if count else 0

    if avg >= 2.5:
        severity = "critical"
    elif avg >= 1.8:
        severity = "high"
    elif avg >= 0.8:
        severity = "medium"
    else:
        severity = "low"

    if report.severity != severity:
        report.severity = severity
        report.save(update_fields=["severity", "updated_at"])


def submit_report(report, submitted_by):
    if report.status not in ("draft",):
        raise ValidationError("Only draft reports can be submitted.")
    report.status = "submitted"
    report.submitted_by = submitted_by
    report.save(update_fields=["status", "submitted_by", "updated_at"])
    return report


def review_report(report, reviewed_by):
    if report.status != "submitted":
        raise ValidationError("Only submitted reports can be reviewed.")
    report.status = "reviewed"
    report.reviewed_by = reviewed_by
    report.save(update_fields=["status", "reviewed_by", "updated_at"])
    return report


def enact_report(report, enacted_by, create_event=False, scheduled_event=None, **event_kwargs):
    """Enact a reviewed report. Optionally create a MobilizationEvent."""
    if report.status not in ("submitted", "reviewed"):
        raise ValidationError("Only submitted or reviewed reports can be enacted.")
    if not can_enact_report(enacted_by, report):
        raise ValidationError("This person does not have permission to enact this report.")

    now = timezone.now()
    report.status = "enacted"
    report.enacted_by = enacted_by
    report.enacted_at = now
    report.save(update_fields=["status", "enacted_by", "enacted_at", "updated_at"])

    event = None
    if create_event:
        event = create_event_from_report(
            report,
            scheduled_event=scheduled_event,
            **event_kwargs,
        )
    return report, event


def reject_report(report, rejected_by, notes=""):
    if report.status not in ("submitted", "reviewed"):
        raise ValidationError("Only submitted or reviewed reports can be rejected.")
    now = timezone.now()
    report.status = "rejected"
    report.rejected_at = now
    if notes:
        report.decision_notes = notes
    report.save(update_fields=["status", "rejected_at", "decision_notes", "updated_at"])
    return report


def create_event_from_report(report, coordinator=None, scheduled_event=None, **kwargs):
    """Create a MobilizationEvent from an enacted report."""
    if report.status != "enacted":
        raise ValidationError("Events can only be created from enacted reports.")
    return MobilizationEvent.objects.create(
        community=report.community,
        source_report=report,
        title=kwargs.pop("title", report.title),
        incident_type=kwargs.pop("incident_type", report.report_type),
        coordinator=coordinator,
        scheduled_event=scheduled_event,
        **kwargs,
    )


def create_deployment(event, community, coordinator=None, **kwargs):
    """Create a Deployment for a MobilizationEvent."""
    if event.community_id != community.pk:
        raise ValidationError("Deployment community must match the event's community.")
    return Deployment.objects.create(
        event=event,
        community=community,
        coordinator=coordinator,
        **kwargs,
    )


def assign_responder_to_deployment(deployment, responder, assigned_by=None, **kwargs):
    """Assign a responder to a deployment."""
    assignment, created = DeploymentAssignment.objects.get_or_create(
        deployment=deployment,
        responder=responder,
        defaults={"assigned_by": assigned_by, **kwargs},
    )
    if not created:
        raise ValidationError("This responder is already assigned to this deployment.")
    return assignment


def activate_deployment_assignment(assignment):
    """Mark an assignment active and set the responder's status to responding."""
    assignment.status = "active"
    assignment.confirmed_at = assignment.confirmed_at or timezone.now()
    assignment.save(update_fields=["status", "confirmed_at"])

    responder = assignment.responder
    responder.current_status = "responding"
    responder.last_status_changed_at = timezone.now()
    responder.save(update_fields=["current_status", "last_status_changed_at", "updated_at"])
    return assignment


def release_responder_from_deployment(assignment):
    """Release a responder from a deployment; set available if no other active assignment."""
    assignment.status = "released"
    assignment.released_at = timezone.now()
    assignment.save(update_fields=["status", "released_at"])

    responder = assignment.responder
    has_active = responder.deployment_assignments.filter(status="active").exists()
    if not has_active:
        responder.current_status = "available"
        responder.last_status_changed_at = timezone.now()
        responder.save(update_fields=["current_status", "last_status_changed_at", "updated_at"])
    return assignment


def create_intervention(deployment, **kwargs):
    """Create an Intervention within a deployment."""
    return Intervention.objects.create(deployment=deployment, **kwargs)


def complete_intervention(intervention, outcome_notes=""):
    intervention.status = "done"
    intervention.completed_at = timezone.now()
    if outcome_notes:
        intervention.outcome_notes = outcome_notes
    intervention.save(update_fields=["status", "completed_at", "outcome_notes", "updated_at"])
    return intervention


def complete_deployment(deployment, force_complete=False):
    """
    Complete a deployment.
    Raises ValidationError if required interventions are not done/cancelled,
    unless force_complete=True.
    """
    if not force_complete:
        blocking = deployment.interventions.filter(
            is_required=True,
        ).exclude(status__in=("done", "cancelled"))
        if blocking.exists():
            titles = list(blocking.values_list("title", flat=True)[:5])
            raise ValidationError(
                f"Cannot complete: {blocking.count()} required intervention(s) still pending: "
                + ", ".join(titles)
            )

    deployment.status = "completed"
    deployment.save(update_fields=["status", "updated_at"])

    # Complete all active assignments
    active_assignments = deployment.assignments.filter(status="active")
    for assignment in active_assignments:
        assignment.status = "completed"
        assignment.save(update_fields=["status"])

        responder = assignment.responder
        has_active = responder.deployment_assignments.filter(status="active").exists()
        if not has_active:
            responder.current_status = "available"
            responder.last_status_changed_at = timezone.now()
            responder.save(update_fields=["current_status", "last_status_changed_at", "updated_at"])

    return deployment
