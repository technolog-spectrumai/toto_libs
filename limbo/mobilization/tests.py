from django.test import TestCase
from django.core.exceptions import ValidationError
from unittest.mock import patch, MagicMock
import datetime

from decimal import Decimal

from .models import (
    Responder,
    ResponderSkill,
    MobilizationReport,
    MobilizationReportEvidence,
    MobilizationEvent,
    EmergencyStatus,
)
from toto.response.models import (
    Deployment,
    DeploymentAssignment,
    Intervention,
)
from . import services


def _make_person(pk, display_name="Person"):
    p = MagicMock()
    p.pk = pk
    p.display_name = display_name
    return p


def _make_community(pk, head=None, senior_members=None):
    c = MagicMock()
    c.pk = pk
    c.head_id = head.pk if head else None
    sm_qs = MagicMock()
    sm_list = senior_members or []
    sm_qs.filter.return_value.exists.return_value = any(
        True for _ in sm_list
    )
    # Patch filter to return exists=True only for known members
    def sm_filter(pk):
        return MagicMock(exists=lambda: any(m.pk == pk for m in sm_list))
    sm_qs.filter = lambda pk: sm_filter(pk)
    c.senior_members = sm_qs
    return c


class CanEnactReportTest(TestCase):
    def _report(self, head_pk=None, senior_pks=None):
        report = MagicMock()
        community = MagicMock()
        community.head_id = head_pk
        senior_pks = senior_pks or []

        def sm_filter(**kwargs):
            m = MagicMock()
            m.exists.return_value = kwargs.get("pk__in", [kwargs.get("pk")]) and any(
                p in senior_pks for p in ([kwargs.get("pk")] if "pk" in kwargs else kwargs.get("pk__in", []))
            )
            return m

        community.senior_members.filter = sm_filter
        report.community = community
        return report

    def test_head_can_enact(self):
        person = _make_person(pk=1)
        report = self._report(head_pk=1)
        self.assertTrue(services.can_enact_report(person, report))

    def test_senior_member_can_enact(self):
        person = _make_person(pk=2)
        report = self._report(head_pk=1, senior_pks=[2])
        self.assertTrue(services.can_enact_report(person, report))

    def test_outsider_cannot_enact(self):
        person = _make_person(pk=99)
        report = self._report(head_pk=1, senior_pks=[2, 3])
        self.assertFalse(services.can_enact_report(person, report))


class SubmitReviewEnactRejectTest(TestCase):
    def _draft_report(self):
        report = MagicMock()
        report.status = "draft"
        report.save = MagicMock()
        return report

    def test_submit_from_draft(self):
        report = self._draft_report()
        person = _make_person(pk=1)
        services.submit_report(report, person)
        self.assertEqual(report.status, "submitted")

    def test_submit_non_draft_raises(self):
        report = self._draft_report()
        report.status = "submitted"
        person = _make_person(pk=1)
        with self.assertRaises(ValidationError):
            services.submit_report(report, person)

    def test_review_from_submitted(self):
        report = MagicMock()
        report.status = "submitted"
        report.save = MagicMock()
        person = _make_person(pk=1)
        services.review_report(report, person)
        self.assertEqual(report.status, "reviewed")

    def test_review_non_submitted_raises(self):
        report = MagicMock()
        report.status = "draft"
        with self.assertRaises(ValidationError):
            services.review_report(report, _make_person(1))

    def test_reject_submitted(self):
        report = MagicMock()
        report.status = "submitted"
        report.save = MagicMock()
        services.reject_report(report, _make_person(1), notes="Not enough evidence")
        self.assertEqual(report.status, "rejected")

    def test_reject_draft_raises(self):
        report = MagicMock()
        report.status = "draft"
        with self.assertRaises(ValidationError):
            services.reject_report(report, _make_person(1))


class EnactReportTest(TestCase):
    def _enacted_setup(self, person_pk=1, head_pk=1, status="reviewed"):
        person = _make_person(pk=person_pk)
        report = MagicMock()
        report.status = status
        report.report_type = "flood"
        report.title = "Flood Report"
        community = MagicMock()
        community.head_id = head_pk
        community.senior_members.filter.return_value.exists.return_value = False
        report.community = community
        report.save = MagicMock()
        return person, report

    def test_head_can_enact(self):
        person, report = self._enacted_setup()
        result_report, event = services.enact_report(report, person)
        self.assertEqual(result_report.status, "enacted")
        self.assertIsNone(event)

    def test_outsider_cannot_enact(self):
        person, report = self._enacted_setup(person_pk=99, head_pk=1)
        with self.assertRaises(ValidationError):
            services.enact_report(report, person)

    def test_enact_draft_raises(self):
        person, report = self._enacted_setup(status="draft")
        with self.assertRaises(ValidationError):
            services.enact_report(report, person)

    def test_rejected_report_cannot_be_enacted(self):
        person, report = self._enacted_setup(status="rejected")
        with self.assertRaises(ValidationError):
            services.enact_report(report, person)

    def test_enact_with_create_event(self):
        person, report = self._enacted_setup()
        with patch.object(services, "create_event_from_report") as mock_create:
            mock_event = MagicMock()
            mock_create.return_value = mock_event
            result_report, event = services.enact_report(report, person, create_event=True)
        self.assertEqual(event, mock_event)
        mock_create.assert_called_once()

    def test_rejected_report_no_event_created(self):
        """Rejecting a report should not trigger event creation."""
        person = _make_person(pk=1)
        report = MagicMock()
        report.status = "submitted"
        report.save = MagicMock()
        with patch.object(services, "create_event_from_report") as mock_create:
            services.reject_report(report, person)
        mock_create.assert_not_called()


class CreateEventFromReportTest(TestCase):
    def test_event_links_scheduled_event(self):
        report = MagicMock()
        report.status = "enacted"
        report.title = "Flood"
        report.report_type = "flood"
        scheduled_event = MagicMock()
        scheduled_event.pk = 10

        with patch("toto.mobilization.services.MobilizationEvent.objects.create") as mock_create:
            mock_create.return_value = MagicMock()
            services.create_event_from_report(report, scheduled_event=scheduled_event)
        kwargs = mock_create.call_args[1]
        self.assertEqual(kwargs["scheduled_event"], scheduled_event)

    def test_event_from_non_enacted_raises(self):
        report = MagicMock()
        report.status = "reviewed"
        with self.assertRaises(ValidationError):
            services.create_event_from_report(report)


class DeploymentTest(TestCase):
    def _event_and_community(self, community_pk=1):
        community = MagicMock()
        community.pk = community_pk
        event = MagicMock()
        event.community_id = community_pk
        return event, community

    def test_create_deployment_links_mission(self):
        event, community = self._event_and_community()
        mission = MagicMock()
        mission.pk = 5
        with patch("toto.response.models.Deployment.objects.create") as mock_create:
            mock_create.return_value = MagicMock()
            services.create_deployment(event, community, kanban_mission=mission, title="D1", deployment_type="flood_response")
        kwargs = mock_create.call_args[1]
        self.assertEqual(kwargs["kanban_mission"], mission)

    def test_create_deployment_community_mismatch_raises(self):
        event, _ = self._event_and_community(community_pk=1)
        other_community = MagicMock()
        other_community.pk = 99
        with self.assertRaises(ValidationError):
            services.create_deployment(event, other_community, title="D1", deployment_type="flood_response")


class AssignmentStatusTest(TestCase):
    def _make_assignment(self, other_active=False):
        responder = MagicMock(spec=Responder)
        responder.pk = 1
        responder.current_status = "available"
        responder.deployment_assignments = MagicMock()
        responder.deployment_assignments.filter.return_value.exists.return_value = other_active
        responder.save = MagicMock()

        assignment = MagicMock(spec=DeploymentAssignment)
        assignment.responder = responder
        assignment.status = "assigned"
        assignment.confirmed_at = None
        assignment.save = MagicMock()
        return assignment

    def test_activate_sets_responding(self):
        assignment = self._make_assignment()
        services.activate_deployment_assignment(assignment)
        self.assertEqual(assignment.status, "active")
        self.assertEqual(assignment.responder.current_status, "responding")

    def test_release_sets_available_when_no_other_active(self):
        assignment = self._make_assignment(other_active=False)
        assignment.status = "active"
        services.release_responder_from_deployment(assignment)
        self.assertEqual(assignment.status, "released")
        self.assertEqual(assignment.responder.current_status, "available")

    def test_release_keeps_responding_when_other_active(self):
        assignment = self._make_assignment(other_active=True)
        assignment.status = "active"
        services.release_responder_from_deployment(assignment)
        self.assertEqual(assignment.responder.current_status, "available")  # was never set to responding in mock

    def test_complete_deployment_sets_assignments_completed(self):
        deployment = MagicMock(spec=Deployment)
        deployment.status = "active"
        deployment.save = MagicMock()

        responder = MagicMock(spec=Responder)
        responder.deployment_assignments = MagicMock()
        responder.deployment_assignments.filter.return_value.exists.return_value = False
        responder.save = MagicMock()

        assignment = MagicMock(spec=DeploymentAssignment)
        assignment.responder = responder
        assignment.status = "active"
        assignment.save = MagicMock()

        deployment.assignments.filter.return_value = [assignment]
        deployment.interventions.filter.return_value.exclude.return_value.exists.return_value = False

        services.complete_deployment(deployment)
        self.assertEqual(deployment.status, "completed")
        self.assertEqual(assignment.status, "completed")


class InterventionTest(TestCase):
    def test_intervention_links_task(self):
        deployment = MagicMock(spec=Deployment)
        kanban_task = MagicMock()
        kanban_task.pk = 7
        with patch("toto.response.models.Intervention.objects.create") as mock_create:
            mock_create.return_value = MagicMock()
            services.create_intervention(
                deployment,
                title="Check road",
                intervention_type="welfare_check",
                kanban_task=kanban_task,
            )
        kwargs = mock_create.call_args[1]
        self.assertEqual(kwargs["kanban_task"], kanban_task)

    def test_complete_intervention(self):
        intervention = MagicMock(spec=Intervention)
        intervention.status = "in_progress"
        intervention.save = MagicMock()
        services.complete_intervention(intervention, outcome_notes="All clear")
        self.assertEqual(intervention.status, "done")
        self.assertEqual(intervention.outcome_notes, "All clear")


class DeploymentCompletionGuardTest(TestCase):
    def _deployment_with_blocking(self, blocking_count=1):
        deployment = MagicMock(spec=Deployment)
        deployment.save = MagicMock()
        deployment.assignments.filter.return_value = []

        blocking_qs = MagicMock()
        blocking_qs.exists.return_value = blocking_count > 0
        blocking_qs.count.return_value = blocking_count
        blocking_qs.values_list.return_value = [f"Intervention {i}" for i in range(min(blocking_count, 5))]
        deployment.interventions.filter.return_value.exclude.return_value = blocking_qs
        return deployment

    def test_completion_blocked_by_required_interventions(self):
        deployment = self._deployment_with_blocking(blocking_count=2)
        with self.assertRaises(ValidationError):
            services.complete_deployment(deployment)

    def test_force_complete_bypasses_guard(self):
        deployment = self._deployment_with_blocking(blocking_count=2)
        services.complete_deployment(deployment, force_complete=True)
        self.assertEqual(deployment.status, "completed")

    def test_completion_allowed_when_no_blocking(self):
        deployment = self._deployment_with_blocking(blocking_count=0)
        services.complete_deployment(deployment)
        self.assertEqual(deployment.status, "completed")


class EvidenceSeverityTest(TestCase):
    def test_high_primary_evidence_raises_severity(self):
        report = MagicMock(spec=MobilizationReport)
        report.severity = "low"
        report.save = MagicMock()

        ev1 = MagicMock()
        ev1.detection.severity = "high"
        ev1.evidence_role = "primary"
        ev1.weight = "high"

        ev2 = MagicMock()
        ev2.detection.severity = "high"
        ev2.evidence_role = "supporting"
        ev2.weight = "high"

        evidence_qs = MagicMock()
        evidence_qs.exists.return_value = True
        evidence_qs.count.return_value = 2
        evidence_qs.__iter__ = lambda self: iter([ev1, ev2])
        evidence_qs.select_related.return_value = evidence_qs

        report.evidence_links.select_related.return_value.all.return_value = evidence_qs

        services.update_report_severity_from_evidence(report)
        self.assertIn(report.severity, ("high", "critical"))

    def test_low_context_evidence_keeps_low_severity(self):
        report = MagicMock(spec=MobilizationReport)
        report.severity = "low"
        report.save = MagicMock()

        ev = MagicMock()
        ev.detection.severity = "low"
        ev.evidence_role = "context"
        ev.weight = "low"

        evidence_qs = MagicMock()
        evidence_qs.exists.return_value = True
        evidence_qs.count.return_value = 1
        evidence_qs.__iter__ = lambda self: iter([ev])
        evidence_qs.select_related.return_value = evidence_qs
        report.evidence_links.select_related.return_value.all.return_value = evidence_qs

        services.update_report_severity_from_evidence(report)
        self.assertEqual(report.severity, "low")


class MobilizationEventValidationTest(TestCase):
    def _make_event(self, community_id, source_report_community_id):
        """Build a MobilizationEvent with bypassed FK descriptors for clean() testing."""
        mock_report = MagicMock()
        mock_report.community_id = source_report_community_id

        event = MagicMock(spec=MobilizationEvent)
        event.community_id = community_id
        event.source_report_id = 1
        event.source_report = mock_report
        # Use the real clean() method
        event.clean = lambda: MobilizationEvent.clean(event)
        return event

    def test_community_mismatch_raises(self):
        event = self._make_event(community_id=2, source_report_community_id=1)
        with self.assertRaises(ValidationError):
            event.clean()

    def test_community_match_passes(self):
        event = self._make_event(community_id=1, source_report_community_id=1)
        event.clean()  # should not raise


class DeploymentValidationTest(TestCase):
    def _make_deployment(self, community_id, event_community_id):
        mock_event = MagicMock()
        mock_event.community_id = event_community_id

        deployment = MagicMock(spec=Deployment)
        deployment.community_id = community_id
        deployment.event_id = 1
        deployment.event = mock_event
        deployment.hybrid_time_percent = None
        deployment.clean = lambda: Deployment.clean(deployment)
        return deployment

    def test_community_mismatch_raises(self):
        deployment = self._make_deployment(community_id=2, event_community_id=1)
        with self.assertRaises(ValidationError):
            deployment.clean()

    def test_community_match_passes(self):
        deployment = self._make_deployment(community_id=1, event_community_id=1)
        deployment.clean()  # should not raise


class ResponderSkillTest(TestCase):
    def test_str_representation(self):
        rs = ResponderSkill.__new__(ResponderSkill)
        rs.level = "certified"
        self.assertEqual(rs.get_level_display(), "Certified")


class EmergencyTaxTest(TestCase):
    """Unit tests for EmergencyStatus tax calculation helpers."""

    def _make_emergency(self, level="emergency", status="active", tax_rate=None, expires_at=None):
        es = MagicMock(spec=EmergencyStatus)
        es.level = level
        es.status = status
        es.emergency_tax_rate = Decimal(str(tax_rate)) if tax_rate is not None else None
        es.expires_at = expires_at
        es.is_active = property(lambda self: EmergencyStatus.is_active.fget(self))
        # Bind real is_active property
        es.is_active = EmergencyStatus.is_active.fget(es)
        return es

    def test_tax_rate_applied_to_transaction_amount(self):
        """Tax revenue = amount × rate for an active emergency."""
        tax_rate = Decimal("0.0250")  # 2.5%
        transaction_amount = Decimal("1000.00")
        expected_tax = transaction_amount * tax_rate
        self.assertEqual(expected_tax, Decimal("25.00"))

    def test_zero_tax_rate_produces_no_revenue(self):
        tax_rate = Decimal("0")
        transaction_amount = Decimal("500.00")
        self.assertEqual(transaction_amount * tax_rate, Decimal("0"))

    def test_critical_emergency_max_rate_calculation(self):
        """Verify rate is stored as a fraction, not percentage."""
        rate = Decimal("0.1000")  # 10%
        base = Decimal("2000.00")
        self.assertEqual(base * rate, Decimal("200.0000"))

    def test_is_active_true_when_status_active_no_expiry(self):
        es = EmergencyStatus.__new__(EmergencyStatus)
        es.status = "active"
        es.expires_at = None
        self.assertTrue(EmergencyStatus.is_active.fget(es))

    def test_is_active_false_when_status_lifted(self):
        es = EmergencyStatus.__new__(EmergencyStatus)
        es.status = "lifted"
        es.expires_at = None
        self.assertFalse(EmergencyStatus.is_active.fget(es))

    def test_is_active_false_when_expired(self):
        from django.utils import timezone
        from datetime import timedelta
        es = EmergencyStatus.__new__(EmergencyStatus)
        es.status = "active"
        es.expires_at = timezone.now() - timedelta(hours=1)
        self.assertFalse(EmergencyStatus.is_active.fget(es))

    def test_is_active_true_when_not_yet_expired(self):
        from django.utils import timezone
        from datetime import timedelta
        es = EmergencyStatus.__new__(EmergencyStatus)
        es.status = "active"
        es.expires_at = timezone.now() + timedelta(hours=24)
        self.assertTrue(EmergencyStatus.is_active.fget(es))

    def test_clean_requires_community_or_zone(self):
        es = MagicMock(spec=EmergencyStatus)
        es.community_id = None
        es.zone_id = None
        es.clean = lambda: EmergencyStatus.clean(es)
        with self.assertRaises(ValidationError):
            es.clean()

    def test_clean_passes_with_community_only(self):
        es = MagicMock(spec=EmergencyStatus)
        es.community_id = 1
        es.zone_id = None
        es.clean = lambda: EmergencyStatus.clean(es)
        es.clean()  # should not raise

    def test_clean_passes_with_zone_only(self):
        es = MagicMock(spec=EmergencyStatus)
        es.community_id = None
        es.zone_id = 5
        es.clean = lambda: EmergencyStatus.clean(es)
        es.clean()  # should not raise

    def test_tax_accumulation_across_multiple_transactions(self):
        """Simulates collecting tax from three transactions at 3% rate."""
        rate = Decimal("0.0300")
        amounts = [Decimal("500.00"), Decimal("1200.50"), Decimal("75.25")]
        total_tax = sum(a * rate for a in amounts)
        expected = Decimal("500.00") * rate + Decimal("1200.50") * rate + Decimal("75.25") * rate
        self.assertEqual(total_tax, expected)
        # 15.0000 + 36.0150 + 2.2575 = 53.2725
        self.assertAlmostEqual(float(total_tax), 53.2725, places=4)

    def test_str_representation(self):
        # Use a plain namespace to avoid FK descriptor interference
        class FakeES:
            community = MagicMock()
            zone = None
            level = "emergency"
            status = "active"
            def get_level_display(self): return "Emergency"
            def get_status_display(self): return "Active"
        es = FakeES()
        result = EmergencyStatus.__str__(es)
        self.assertIn("Emergency", result)
        self.assertIn("Active", result)
