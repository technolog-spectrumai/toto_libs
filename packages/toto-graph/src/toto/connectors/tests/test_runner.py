import tempfile
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings

from toto.bento import graph_service
from toto.connectors.models import ConnectorRun
from toto.connectors.services import runner
from toto.connectors.services.extract import ExtractError, ExtractResult, FetchPage
from toto.ingestor.models import IngestProposal
from toto.ingestor.services import catalog as catalog_svc

from .helpers import make_bento_templates, make_data_connector

MEDIA_ROOT = tempfile.mkdtemp(prefix="connectors-test-media-")

RECORDS = [{"author": {"name": "Ada Lovelace"}, "org": {"name": "Analytical Engines"}}]


def _extract_result(records=None):
    records = RECORDS if records is None else records
    page = FetchPage(
        url="https://api.example.com/items",
        status_code=200,
        json={"results": records},
        record_count=len(records),
    )
    return ExtractResult(
        records=records,
        pages=[page],
        stats={"pages": 1, "records": len(records), "capped": False},
    )


class _FakeExtractor:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def validate_config(self, config):
        return []

    def extract(self, *, data_connector, vault_session=None):
        if self.error:
            raise self.error
        return self.result


@override_settings(RAVIOLI_ENABLED=True, MEDIA_ROOT=MEDIA_ROOT)
class ExecuteRunTests(TestCase):
    def setUp(self):
        make_bento_templates()
        self.user = User.objects.create_superuser("admin", password="x")
        self.connector = make_data_connector(owner=self.user)
        self.catalog_patch = patch.object(
            catalog_svc, "build_catalog",
            return_value=([], {"node_count": 0, "entry_count": 0, "truncated": False}),
        )
        self.catalog_patch.start()
        self.addCleanup(self.catalog_patch.stop)

    def _run(self, extractor):
        run = runner.create_run(
            self.connector, triggered=ConnectorRun.TRIGGERED_MANUAL, user=self.user
        )
        with patch.object(runner, "get_extractor", return_value=extractor):
            runner.execute_run(run.pk)
        run.refresh_from_db()
        return run

    def test_untrusted_run_ends_in_review(self):
        run = self._run(_FakeExtractor(result=_extract_result()))
        self.assertEqual(run.status, ConnectorRun.STATUS_REVIEW)
        self.assertIsNotNone(run.proposal)
        self.assertEqual(run.proposal.status, IngestProposal.STATUS_READY)
        self.assertEqual(run.proposal.summary["strategy"], "connector")
        self.assertIn("[connector:", run.proposal.source_text)
        self.assertIsNotNone(run.raw_payload_file)
        self.assertEqual(run.request_log[0]["status_code"], 200)
        self.assertNotIn("json", run.request_log[0])  # bodies only in the archive
        self.assertEqual(run.stats["new_nodes"], 2)
        self.assertEqual(run.stats["relationships"], 1)
        self.assertIsNotNone(run.started_at)
        self.assertIsNotNone(run.finished_at)

    def test_archived_payload_contains_pages(self):
        import json as jsonlib

        run = self._run(_FakeExtractor(result=_extract_result()))
        with run.raw_payload_file.file.open("rb") as fh:
            envelope = jsonlib.loads(fh.read().decode("utf-8"))
        self.assertEqual(envelope["connector"], self.connector.slug)
        self.assertEqual(envelope["run_id"], run.pk)
        self.assertEqual(envelope["pages"][0]["json"]["results"], RECORDS)

    def test_trusted_run_applies_valid_elements(self):
        self.connector.trusted = True
        self.connector.save()
        created = iter([{"uid": "u1"}, {"uid": "u2"}])
        with patch.object(graph_service, "create_node", side_effect=lambda *a, **k: next(created)) as cn, \
             patch.object(graph_service, "create_edge", return_value={"id": "e1"}) as ce:
            run = self._run(_FakeExtractor(result=_extract_result()))
        self.assertEqual(run.status, ConnectorRun.STATUS_APPLIED)
        self.assertEqual(run.proposal.status, IngestProposal.STATUS_APPLIED)
        self.assertEqual(cn.call_count, 2)
        self.assertEqual(ce.call_count, 1)
        self.assertEqual(
            set(run.proposal.apply_result["node_uids"].values()), {"u1", "u2"}
        )

    def test_trusted_run_never_applies_invalid_elements(self):
        # works-at only allows person → org; a person → person edge fails
        # Bento validation, so approve_all_valid must leave it unapproved and
        # apply must never write it — while the valid nodes still land.
        self.connector.trusted = True
        self.connector.mapping_spec = {
            "version": 1,
            "nodes": [
                {"rule_id": "p1", "category_slug": "person",
                 "identifier": {"path": "author.name"}},
                {"rule_id": "p2", "category_slug": "person",
                 "identifier": {"path": "org.name"}},
            ],
            "relationships": [{
                "rule_id": "bad", "edge_type_slug": "works-at",
                "from_rule": "p1", "to_rule": "p2",
            }],
        }
        self.connector.save()
        created = iter([{"uid": "u1"}, {"uid": "u2"}])
        with patch.object(graph_service, "create_node", side_effect=lambda *a, **k: next(created)) as cn, \
             patch.object(graph_service, "create_edge") as ce:
            run = self._run(_FakeExtractor(result=_extract_result()))
        self.assertEqual(cn.call_count, 2)
        ce.assert_not_called()
        self.assertEqual(run.status, ConnectorRun.STATUS_APPLIED)
        rel = run.proposal.proposal["relationships"][0]
        self.assertEqual(rel["validation"]["status"], "error")
        self.assertNotEqual(rel["approval"], "approved")

    def test_extractor_failure_fails_the_run(self):
        run = self._run(_FakeExtractor(error=ExtractError("upstream 500")))
        self.assertEqual(run.status, ConnectorRun.STATUS_FAILED)
        self.assertIn("upstream 500", run.error)
        self.assertIsNone(run.proposal)

    def test_zero_records_is_empty(self):
        run = self._run(_FakeExtractor(result=_extract_result(records=[])))
        self.assertEqual(run.status, ConnectorRun.STATUS_EMPTY)
        self.assertIsNone(run.proposal)

    def test_execute_run_is_idempotent_on_status(self):
        run = self._run(_FakeExtractor(result=_extract_result()))
        proposal_id = run.proposal_id
        # a second execution must be a no-op (double-dispatch guard)
        with patch.object(runner, "get_extractor") as ge:
            runner.execute_run(run.pk)
        ge.assert_not_called()
        run.refresh_from_db()
        self.assertEqual(run.proposal_id, proposal_id)

    def test_create_run_refuses_stacking(self):
        runner.create_run(self.connector, triggered=ConnectorRun.TRIGGERED_MANUAL)
        with self.assertRaises(runner.RunInProgress):
            runner.create_run(self.connector, triggered=ConnectorRun.TRIGGERED_MANUAL)


class VaultSessionTests(TestCase):
    def test_no_auth_connector_needs_no_vault(self):
        connector = make_data_connector()
        self.assertIsNone(runner._vault_session(connector))

    def test_authed_connector_without_vault_raises_actionable_error(self):
        from toto.api.models import Connector

        connector = make_data_connector()
        connector.http_connector.auth_type = Connector.AUTH_BEARER_TOKEN
        connector.http_connector.save()
        from toto.sabbia import vault

        with patch.object(vault, "open_session", side_effect=vault.VaultUnavailable("locked")):
            with self.assertRaises(runner.RunnerError) as ctx:
                runner._vault_session(connector)
        self.assertIn("connectors_init_vault", str(ctx.exception))
