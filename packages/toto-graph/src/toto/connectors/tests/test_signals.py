from unittest.mock import patch

from django.test import TestCase, override_settings

from toto.bento import graph_service
from toto.connectors.models import ConnectorRun
from toto.ingestor.models import IngestProposal
from toto.ingestor.services import apply as apply_svc

from .helpers import make_bento_templates, make_data_connector


@override_settings(RAVIOLI_ENABLED=True)
class ProposalSignalTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.connector = make_data_connector()
        self.proposal = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={
                "nodes": [{
                    "temp_id": "n1", "kind": "new", "category_slug": "person",
                    "display": "Ada", "properties": {"name": "Ada"},
                    "approval": "approved", "validation": {"status": "ok", "errors": []},
                }],
                "relationships": [],
            },
            summary={},
        )
        self.run = ConnectorRun.objects.create(
            connector=self.connector,
            status=ConnectorRun.STATUS_REVIEW,
            proposal=self.proposal,
        )

    def test_human_apply_flips_run_to_applied(self):
        with patch.object(graph_service, "create_node", return_value={"uid": "u1"}):
            _result, errors = apply_svc.run(self.proposal)
        self.assertEqual(errors, [])
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, ConnectorRun.STATUS_APPLIED)
        self.assertIsNotNone(self.run.finished_at)

    def test_failed_apply_flips_run_to_failed(self):
        with patch.object(graph_service, "create_node", side_effect=RuntimeError("boom")):
            _result, errors = apply_svc.run(self.proposal)
        self.assertTrue(errors)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, ConnectorRun.STATUS_FAILED)
        self.assertIn("boom", self.run.error)

    def test_ordinary_ingestor_proposals_do_not_touch_runs(self):
        other = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []},
        )
        other.status = IngestProposal.STATUS_APPLIED
        other.save()
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, ConnectorRun.STATUS_REVIEW)

    def test_review_status_proposal_does_not_flip_run(self):
        self.proposal.save()  # still READY
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, ConnectorRun.STATUS_REVIEW)
