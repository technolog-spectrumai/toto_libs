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

    @override_settings(RAVIOLI_ENABLED=True, OPENAI_API_KEY="sk-x")
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
    def test_generate_passes_include_existing_nodes(self):
        from toto.ingestor.services.strategies import IngestStrategy

        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []}, summary={},
        )
        with patch.object(IngestStrategy.get("deterministic"), "run", return_value=obj) as run:
            self.client.post(reverse("ingestor:generate"),
                             {"text": "x", "strategy": "deterministic", "include_existing_nodes": "1"})
        self.assertTrue(run.call_args.kwargs.get("include_existing_nodes"))

    @override_settings(RAVIOLI_ENABLED=True)
    def test_generate_include_existing_nodes_defaults_false(self):
        from toto.ingestor.services.strategies import IngestStrategy

        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []}, summary={},
        )
        with patch.object(IngestStrategy.get("deterministic"), "run", return_value=obj) as run:
            self.client.post(reverse("ingestor:generate"), {"text": "x", "strategy": "deterministic"})
        self.assertFalse(run.call_args.kwargs.get("include_existing_nodes"))

    @override_settings(RAVIOLI_ENABLED=True)
    def test_generate_unknown_strategy_400(self):
        res = self.client.post(reverse("ingestor:generate"), {"text": "x", "strategy": "nope"})
        self.assertEqual(res.status_code, 400)

    @override_settings(RAVIOLI_ENABLED=True, OPENAI_API_KEY="")
    def test_generate_rejects_unavailable_strategy(self):
        # 'llm' needs an OpenAI key; without one it must be rejected, not run.
        res = self.client.post(reverse("ingestor:generate"), {"text": "x", "strategy": "llm"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("unavailable", res.json()["error"])

    @override_settings(OPENAI_API_KEY="", VICUNA_EMBEDDINGS_ENABLED=False)
    def test_list_strategies_hides_unconfigured_backends(self):
        # No OpenAI key, no local embeddings → only the deterministic strategy.
        res = self.client.get(reverse("ingestor:strategies"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual({s["key"] for s in res.json()["strategies"]}, {"deterministic"})

    @override_settings(OPENAI_API_KEY="sk-x", VICUNA_EMBEDDINGS_ENABLED=False)
    def test_list_strategies_shows_llm_but_not_kg_builder_without_embeddings(self):
        keys = {s["key"] for s in self.client.get(reverse("ingestor:strategies")).json()["strategies"]}
        self.assertIn("llm", keys)
        self.assertNotIn("kg-builder", keys)

    @override_settings(OPENAI_API_KEY="sk-x", VICUNA_EMBEDDINGS_ENABLED=True)
    def test_list_strategies_shows_kg_builder_when_vicuna_present(self):
        keys = {s["key"] for s in self.client.get(reverse("ingestor:strategies")).json()["strategies"]}
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
        from toto.ingestor.services.approval import approve_all_valid

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
        # revalidate/save_edits hit Neo4j/DB — stub them; we test the approve loop.
        with patch("toto.ingestor.services.approval.revalidate"), \
             patch("toto.ingestor.services.approval.save_edits"):
            approve_all_valid(obj)
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
        with patch("toto.ingestor.views.approve_all_valid") as approve, \
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
        with patch("toto.ingestor.views.approve_all_valid") as approve, \
             patch("toto.ingestor.services.apply.run", return_value=({}, [])):
            res = self.client.post(reverse("ingestor:apply", args=[obj.id]))
        self.assertEqual(res.status_code, 200)
        approve.assert_not_called()


class SetApprovalTests(TestCase):
    """Bulk Select-all / Clear-selection endpoint."""

    def setUp(self):
        User.objects.create_superuser("admin", password="x")
        self.client.login(username="admin", password="x")
        self.obj = IngestProposal.objects.create(
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
            summary={},
        )

    def test_select_all_approves_valid_only(self):
        url = reverse("ingestor:set_approval", args=[self.obj.id])
        # revalidate hits Bento/Neo4j — stub it so the pre-set validation stands.
        with patch("toto.ingestor.services.approval.revalidate"):
            res = self.client.post(
                url, data=json.dumps({"approval": "approved"}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        nodes = {n["temp_id"]: n for n in body["proposal"]["nodes"]}
        self.assertEqual(nodes["n1"]["approval"], "approved")
        self.assertEqual(nodes["n2"]["approval"], "pending")  # error → skipped
        self.assertEqual(body["proposal"]["relationships"][0]["approval"], "approved")
        self.assertEqual(body["summary"]["approved"], 2)

    def test_clear_selection_resets_to_pending(self):
        for n in self.obj.proposal["nodes"]:
            n["approval"] = "approved"
        self.obj.save(update_fields=["proposal"])
        url = reverse("ingestor:set_approval", args=[self.obj.id])
        with patch("toto.ingestor.services.approval.revalidate"):
            res = self.client.post(
                url, data=json.dumps({"approval": "pending"}),
                content_type="application/json",
            )
        self.assertEqual(res.status_code, 200)
        nodes = {n["temp_id"]: n for n in res.json()["proposal"]["nodes"]}
        self.assertEqual(nodes["n1"]["approval"], "pending")
        self.assertEqual(res.json()["summary"]["approved"], 0)

    def test_invalid_approval_value_400(self):
        url = reverse("ingestor:set_approval", args=[self.obj.id])
        res = self.client.post(
            url, data=json.dumps({"approval": "bogus"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_set_approval_on_applied_proposal_409(self):
        self.obj.status = IngestProposal.STATUS_APPLIED
        self.obj.save(update_fields=["status"])
        url = reverse("ingestor:set_approval", args=[self.obj.id])
        res = self.client.post(
            url, data=json.dumps({"approval": "approved"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 409)
