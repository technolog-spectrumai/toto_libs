import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.bento.models import BentoCategory
from toto.core.models import Platform
from toto.ingestor.models import IngestProposal
from toto.ingestor.services import pipeline


class PermissionTests(TestCase):
    def setUp(self):
        # PageProcessor (used by the page render) 404s without an active Platform.
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)

    def test_home_requires_login(self):
        res = self.client.get(reverse("ingestor:home"))
        self.assertEqual(res.status_code, 302)

    def test_home_requires_superuser(self):
        User.objects.create_user("bob", password="x")
        self.client.login(username="bob", password="x")
        res = self.client.get(reverse("ingestor:home"))
        self.assertEqual(res.status_code, 302)

    def test_home_ok_for_superuser(self):
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        res = self.client.get(reverse("ingestor:home"))
        self.assertEqual(res.status_code, 200)


class GenerateTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    @override_settings(RAVIOLI_ENABLED=False)
    def test_generate_blocked_when_graph_disabled(self):
        res = self.client.post(reverse("ingestor:generate"), {"text": "hi"})
        self.assertEqual(res.status_code, 503)

    @override_settings(RAVIOLI_ENABLED=True)
    def test_generate_rejects_empty_text(self):
        res = self.client.post(reverse("ingestor:generate"), {"text": "   "})
        self.assertEqual(res.status_code, 400)

    @override_settings(RAVIOLI_ENABLED=True)
    def test_generate_happy_path(self):
        from toto.ingestor.services.strategies import IngestStrategy

        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []},
            summary={"total_changes": 0},
        )
        # No strategy param → defaults to "deterministic".
        with patch.object(IngestStrategy.get("deterministic"), "run", return_value=obj) as gen:
            res = self.client.post(reverse("ingestor:generate"), {"text": "Ada works at Acme."})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["proposal_id"], obj.id)
        gen.assert_called_once()

    @override_settings(RAVIOLI_ENABLED=True)
    def test_generate_dispatches_selected_strategy(self):
        from toto.ingestor.services.strategies import IngestStrategy

        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []}, summary={},
        )
        with patch.object(IngestStrategy.get("llm"), "run", return_value=obj) as run:
            res = self.client.post(reverse("ingestor:generate"), {"text": "x", "strategy": "llm"})
        self.assertEqual(res.status_code, 200)
        run.assert_called_once()

    @override_settings(RAVIOLI_ENABLED=True)
    def test_generate_unknown_strategy_400(self):
        res = self.client.post(reverse("ingestor:generate"), {"text": "x", "strategy": "nope"})
        self.assertEqual(res.status_code, 400)

    def test_list_strategies(self):
        res = self.client.get(reverse("ingestor:strategies"))
        self.assertEqual(res.status_code, 200)
        keys = {s["key"] for s in res.json()["strategies"]}
        self.assertEqual(keys, {"deterministic", "llm", "kg-builder"})


class EditTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        BentoCategory.objects.create(
            name="Person", slug="person", neo4j_label="Person",
            property_schema=[{"name": "name", "type": "string", "required": True}],
        )
        self.obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={
                "nodes": [{
                    "temp_id": "n1", "kind": "new", "category_slug": "person",
                    "display": "Ada", "properties": {"name": "Ada"},
                    "approval": "pending", "merge_into_uid": None, "uid": None,
                }],
                "relationships": [],
            },
        )

    def test_patch_node_approval_updates_summary(self):
        url = reverse("ingestor:patch_node", args=[self.obj.id, "n1"])
        res = self.client.post(url, data=json.dumps({"approval": "approved"}),
                               content_type="application/json")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["node"]["approval"], "approved")
        self.assertEqual(body["node"]["validation"]["status"], "ok")
        self.assertEqual(body["summary"]["approved"], 1)

    def test_patch_unknown_node_404(self):
        url = reverse("ingestor:patch_node", args=[self.obj.id, "nX"])
        res = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(res.status_code, 404)


class ApplyAllTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")

    def test_approve_all_valid_skips_errors(self):
        from toto.ingestor.views import _approve_all_valid

        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={
                "nodes": [
                    {"temp_id": "n1", "approval": "pending", "validation": {"status": "ok"}},
                    {"temp_id": "n2", "approval": "pending", "validation": {"status": "error"}},
                ],
                "relationships": [
                    {"temp_id": "r1", "approval": "pending", "validation": {"status": "ok"}},
                ],
            },
        )
        # revalidate/_save_edits hit Neo4j/DB — stub them; we test the approve loop.
        with patch("toto.ingestor.views.revalidate"), patch("toto.ingestor.views._save_edits"):
            _approve_all_valid(obj)
        nodes = {n["temp_id"]: n for n in obj.proposal["nodes"]}
        self.assertEqual(nodes["n1"]["approval"], "approved")
        self.assertEqual(nodes["n2"]["approval"], "pending")  # error → left unapproved
        self.assertEqual(obj.proposal["relationships"][0]["approval"], "approved")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_apply_view_approve_all_then_applies(self):
        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []}, summary={},
        )
        with patch("toto.ingestor.views._approve_all_valid") as approve, \
             patch("toto.ingestor.services.apply.run", return_value=({}, [])) as run:
            res = self.client.post(reverse("ingestor:apply", args=[obj.id]), {"approve_all": "1"})
        self.assertEqual(res.status_code, 200)
        approve.assert_called_once()
        run.assert_called_once()

    @override_settings(RAVIOLI_ENABLED=True)
    def test_apply_view_without_approve_all_skips_bulk_approve(self):
        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []}, summary={},
        )
        with patch("toto.ingestor.views._approve_all_valid") as approve, \
             patch("toto.ingestor.services.apply.run", return_value=({}, [])):
            res = self.client.post(reverse("ingestor:apply", args=[obj.id]))
        self.assertEqual(res.status_code, 200)
        approve.assert_not_called()
