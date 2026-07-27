"""Apply path: approval/validation gating + idempotent/resumable writes.

Neo4j is mocked (``graph_service.create_node``/``create_edge``); Bento templates
live in SQL so re-validation runs for real.
"""

from unittest.mock import patch

from django.test import TestCase

from toto.bento import graph_service as gs
from toto.bento.models import BentoCategory, BentoEdgeType
from toto.ingestor.models import IngestProposal
from toto.ingestor.services import apply as apply_svc


def _proposal():
    return {
        "nodes": [
            {"temp_id": "n1", "kind": "new", "category_slug": "person",
             "display": "Ada", "properties": {"name": "Ada"}, "approval": "approved",
             "merge_into_uid": None, "uid": None},
            {"temp_id": "n2", "kind": "existing", "category_slug": "organization",
             "display": "Acme", "properties": {}, "approval": "pending",
             "merge_into_uid": None, "uid": "u-acme"},
            {"temp_id": "n3", "kind": "new", "category_slug": "person",
             "display": "Bob", "properties": {"name": "Bob"}, "approval": "rejected",
             "merge_into_uid": None, "uid": None},
        ],
        "relationships": [
            {"temp_id": "r1", "edge_type_slug": "works-at", "rel_type": "WORKS_AT",
             "from": "n1", "to": "n2", "properties": {}, "approval": "approved"},
        ],
    }


class ApplyTests(TestCase):
    def setUp(self):
        self.person = BentoCategory.objects.create(
            name="Person", slug="person", neo4j_label="Person",
            property_schema=[{"name": "name", "type": "string", "required": True}],
        )
        self.org = BentoCategory.objects.create(
            name="Org", slug="organization", neo4j_label="Organization",
            property_schema=[{"name": "name", "type": "string", "required": True}],
        )
        self.works_at = BentoEdgeType.objects.create(
            name="Works at", slug="works-at", rel_type="WORKS_AT",
        )
        self.works_at.allowed_sources.set([self.person])
        self.works_at.allowed_targets.set([self.org])

    def test_applies_only_approved_and_valid(self):
        proposal = _proposal()
        with patch.object(gs, "create_node", return_value={"uid": "new-1"}) as cn, \
             patch.object(gs, "create_edge", return_value={"id": "e1"}) as ce:
            result, errors = apply_svc.apply_proposal(proposal)

        self.assertEqual(errors, [])
        cn.assert_called_once_with("person", {"name": "Ada"})        # n3 rejected → skipped
        ce.assert_called_once_with("works-at", "new-1", "u-acme", {})
        self.assertEqual(result["node_uids"]["n1"], "new-1")
        self.assertEqual(result["node_uids"]["n2"], "u-acme")        # reference resolved
        self.assertEqual(result["relationships"], ["r1"])

    def test_resume_does_not_recreate(self):
        proposal = _proposal()
        prior = {"node_uids": {"n1": "new-1", "n2": "u-acme"}, "relationships": ["r1"], "errors": []}
        with patch.object(gs, "create_node") as cn, patch.object(gs, "create_edge") as ce:
            _result, errors = apply_svc.apply_proposal(proposal, prior)
        cn.assert_not_called()
        ce.assert_not_called()
        self.assertEqual(errors, [])

    def test_rel_without_applied_endpoint_reports_error(self):
        proposal = _proposal()
        proposal["nodes"][0]["approval"] = "pending"  # n1 not applied → r1 endpoint missing
        with patch.object(gs, "create_node", return_value={"uid": "new-1"}), \
             patch.object(gs, "create_edge") as ce:
            _result, errors = apply_svc.apply_proposal(proposal)
        ce.assert_not_called()
        self.assertTrue(any("endpoint" in e for e in errors))

    def test_run_marks_proposal_applied(self):
        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY, proposal=_proposal(),
        )
        with patch.object(gs, "create_node", return_value={"uid": "new-1"}), \
             patch.object(gs, "create_edge", return_value={"id": "e1"}):
            _result, errors = apply_svc.run(obj)
        obj.refresh_from_db()
        self.assertEqual(errors, [])
        self.assertEqual(obj.status, IngestProposal.STATUS_APPLIED)
        self.assertIsNotNone(obj.applied_at)
