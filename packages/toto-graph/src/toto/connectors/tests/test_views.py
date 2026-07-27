import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.connectors.models import ConnectorRun
from toto.core.models import Platform
from toto.ingestor.models import IngestProposal

from .helpers import make_bento_templates, make_data_connector


class PermissionTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        self.connector = make_data_connector()
        self.run = ConnectorRun.objects.create(connector=self.connector)

    def _routes(self):
        return [
            ("get", reverse("connectors:home")),
            ("get", reverse("connectors:new")),
            ("get", reverse("connectors:detail", args=[self.connector.pk])),
            ("post", reverse("connectors:run_now", args=[self.connector.pk])),
            ("post", reverse("connectors:validate_config")),
            ("get", reverse("connectors:run_detail", args=[self.run.pk])),
            ("get", reverse("connectors:run_review", args=[self.run.pk])),
            ("get", reverse("connectors:run_status", args=[self.run.pk])),
        ]

    def test_anonymous_is_redirected_everywhere(self):
        for method, url in self._routes():
            res = getattr(self.client, method)(url)
            self.assertEqual(res.status_code, 302, url)

    def test_non_superuser_is_redirected_everywhere(self):
        User.objects.create_user("bob", password="x")
        self.client.login(username="bob", password="x")
        for method, url in self._routes():
            res = getattr(self.client, method)(url)
            self.assertEqual(res.status_code, 302, url)


class SuperuserViewTests(TestCase):
    def setUp(self):
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        make_bento_templates()
        self.user = User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.connector = make_data_connector()

    def test_home_lists_connectors(self):
        res = self.client.get(reverse("connectors:home"))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, self.connector.name)

    def test_run_now_dispatches_and_redirects_to_run(self):
        with patch("toto.connectors.views.dispatch_run", return_value=True) as dispatch:
            res = self.client.post(reverse("connectors:run_now", args=[self.connector.pk]))
        self.assertEqual(res.status_code, 302)
        dispatch.assert_called_once()
        run = self.connector.runs.get()
        self.assertEqual(run.triggered, ConnectorRun.TRIGGERED_MANUAL)
        self.assertEqual(run.created_by, self.user)
        self.assertIn(str(run.pk), res["Location"])

    def test_run_now_with_run_in_flight_warns_and_redirects_home(self):
        ConnectorRun.objects.create(connector=self.connector)
        with patch("toto.connectors.views.dispatch_run") as dispatch:
            res = self.client.post(reverse("connectors:run_now", args=[self.connector.pk]))
        dispatch.assert_not_called()
        self.assertEqual(res["Location"], reverse("connectors:home"))
        self.assertEqual(self.connector.runs.count(), 1)

    def test_run_detail_renders_audit(self):
        run = ConnectorRun.objects.create(
            connector=self.connector,
            status=ConnectorRun.STATUS_FAILED,
            error="upstream exploded",
            request_log=[{"url": "https://api.example.com/items?q=x",
                          "status_code": 500, "records": 0, "truncated": False}],
        )
        res = self.client.get(reverse("connectors:run_detail", args=[run.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "upstream exploded")
        self.assertContains(res, "https://api.example.com/items?q=x")

    @override_settings(RAVIOLI_ENABLED=False)
    def test_run_review_boots_ingestor_ui_with_initial_payload(self):
        proposal = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []},
            summary={},
        )
        run = ConnectorRun.objects.create(
            connector=self.connector,
            status=ConnectorRun.STATUS_REVIEW,
            proposal=proposal,
        )
        res = self.client.get(reverse("connectors:run_review", args=[run.pk]))
        self.assertEqual(res.status_code, 200)
        self.assertContains(res, "ingestor-initial")
        self.assertContains(res, f'"proposal_id": {proposal.pk}')

    def test_run_review_without_proposal_redirects_to_detail(self):
        run = ConnectorRun.objects.create(
            connector=self.connector, status=ConnectorRun.STATUS_EMPTY
        )
        res = self.client.get(reverse("connectors:run_review", args=[run.pk]))
        self.assertEqual(res["Location"], reverse("connectors:run_detail", args=[run.pk]))

    def test_run_status_syncs_from_proposal(self):
        proposal = IngestProposal.objects.create(
            status=IngestProposal.STATUS_APPLIED,
            proposal={"nodes": [], "relationships": []},
        )
        run = ConnectorRun.objects.create(
            connector=self.connector,
            status=ConnectorRun.STATUS_REVIEW,
            proposal=proposal,
        )
        res = self.client.get(reverse("connectors:run_status", args=[run.pk]))
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["status"], ConnectorRun.STATUS_APPLIED)
        self.assertEqual(body["proposal_id"], proposal.pk)

    def test_validate_config_reports_both_error_lists(self):
        payload = {
            "extractor_kind": "rest_api",
            "extract_config": json.dumps({"method": "DELETE"}),
            "mapping_spec": json.dumps({"version": 1, "nodes": []}),
        }
        res = self.client.post(
            reverse("connectors:validate_config"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["mapping_errors"])
        self.assertTrue(body["extract_errors"])

    def test_validate_config_accepts_valid_configs(self):
        from .helpers import DEFAULT_EXTRACT_CONFIG, DEFAULT_MAPPING_SPEC

        payload = {
            "extractor_kind": "rest_api",
            "extract_config": json.dumps(DEFAULT_EXTRACT_CONFIG),
            "mapping_spec": json.dumps(DEFAULT_MAPPING_SPEC),
        }
        res = self.client.post(
            reverse("connectors:validate_config"),
            data=json.dumps(payload),
            content_type="application/json",
        )
        body = res.json()
        self.assertEqual(body["mapping_errors"], [])
        self.assertEqual(body["extract_errors"], [])

    def test_connector_edit_saves(self):
        res = self.client.post(
            reverse("connectors:detail", args=[self.connector.pk]),
            {
                "name": "Renamed connector",
                "description": "",
                "http_connector": self.connector.http_connector.pk,
                "extractor_kind": "rest_api",
                "extract_config": json.dumps(self.connector.extract_config),
                "mapping_spec": json.dumps(self.connector.mapping_spec),
                "interval_minutes": "",
            },
        )
        self.assertEqual(res.status_code, 302)
        self.connector.refresh_from_db()
        self.assertEqual(self.connector.name, "Renamed connector")

    def test_connector_edit_rejects_invalid_mapping(self):
        res = self.client.post(
            reverse("connectors:detail", args=[self.connector.pk]),
            {
                "name": self.connector.name,
                "description": "",
                "http_connector": self.connector.http_connector.pk,
                "extractor_kind": "rest_api",
                "extract_config": json.dumps(self.connector.extract_config),
                "mapping_spec": json.dumps({"version": 1, "nodes": [
                    {"rule_id": "x", "category_slug": "ghost", "identifier": {"path": "p"}}
                ]}),
                "interval_minutes": "",
            },
        )
        self.assertEqual(res.status_code, 200)  # re-rendered with errors
        self.assertContains(res, "unknown category")
