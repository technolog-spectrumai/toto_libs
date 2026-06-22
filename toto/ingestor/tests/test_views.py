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
        obj = IngestProposal.objects.create(
            status=IngestProposal.STATUS_READY,
            proposal={"nodes": [], "relationships": []},
            summary={"total_changes": 0},
        )
        with patch.object(pipeline, "generate", return_value=obj) as gen:
            res = self.client.post(reverse("ingestor:generate"), {"text": "Ada works at Acme."})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["proposal_id"], obj.id)
        gen.assert_called_once()


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
