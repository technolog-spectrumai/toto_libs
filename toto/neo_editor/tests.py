import json

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from toto.ravioli import neojson


def _sample_graph():
    return neojson.from_ravioli(
        [
            {"id": "n1", "labels": ["Person"], "props": {"name": "Alice"}},
            {"id": "n2", "labels": ["Person"], "props": {"name": "Bob"}},
        ],
        [{"id": "rel-1", "type": "KNOWS", "start": "n1", "end": "n2", "props": {}}],
    )


class NeoJsonEditorPluginTests(SimpleTestCase):
    """The .neojson file type registers the neo_editor vault editor."""

    def test_plugin_registered(self):
        from toto.vault.plugins import VaultEditorPlugin

        plugin = VaultEditorPlugin.for_file_type("neojson")
        self.assertIsNotNone(plugin)
        self.assertEqual(plugin.file_type, "neojson")

    def test_plugin_editor_url_points_to_neo_editor(self):
        from toto.vault.plugins import VaultEditorPlugin

        plugin = VaultEditorPlugin.for_file_type("neojson")

        class _FakeFile:
            pk = 7

        self.assertEqual(plugin.get_editor_url(_FakeFile()), reverse("neo_editor:neojson_editor", args=[7]))


class NeoJsonEditorViewTests(TestCase):
    """The dual-mode editor view + its save endpoint."""

    def setUp(self):
        from toto.core.models import Platform
        from toto.vault.models import Bucket, VaultFile

        # The editor renders a full page via PageProcessor, which needs an active Platform.
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)

        self.user = User.objects.create_user("neojson_owner", password="pw")
        self.other = User.objects.create_user("neojson_other", password="pw")
        self.bucket = Bucket.objects.create(name="Editor Bucket", owner=self.user, slug="editor-bucket")

        self.vf = VaultFile(
            owner=self.user, title="g.neojson", bucket=self.bucket,
            file_type="neojson", key="g-neojson",
        )
        self.vf.file.save("g.neojson", ContentFile(neojson.dumps(_sample_graph()).encode()), save=False)
        self.vf.save()

        self.client = Client()

    def test_editor_get_owner_ok(self):
        self.client.force_login(self.user)
        resp = self.client.get(reverse("neo_editor:neojson_editor", args=[self.vf.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "neo_editor/neojson_editor.html")

    def test_editor_get_non_owner_404(self):
        self.client.force_login(self.other)
        resp = self.client.get(reverse("neo_editor:neojson_editor", args=[self.vf.pk]))
        self.assertEqual(resp.status_code, 404)

    def test_save_valid_content(self):
        self.client.force_login(self.user)
        graph = neojson.from_ravioli([{"id": "x1", "labels": ["Thing"], "props": {"n": 1}}], [])
        new_content = neojson.dumps(graph)
        resp = self.client.post(
            reverse("neo_editor:neojson_save", args=[self.vf.pk]),
            {"content": new_content},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "ok")

        self.vf.refresh_from_db()
        with self.vf.file.open("rb") as fh:
            saved = neojson.loads(fh.read().decode("utf-8"))
        self.assertEqual({n.id for n in saved.nodes}, {"x1"})

    def test_save_invalid_json_rejected(self):
        self.client.force_login(self.user)
        resp = self.client.post(
            reverse("neo_editor:neojson_save", args=[self.vf.pk]),
            {"content": "{not json"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_save_dangling_relationship_rejected(self):
        self.client.force_login(self.user)
        bad = json.dumps({
            "neojson": "1.0", "type": "Graph",
            "nodes": [{"type": "Node", "id": "n1"}],
            "relationships": [
                {"type": "Relationship", "relType": "KNOWS", "start": "n1", "end": "ghost"}
            ],
        })
        resp = self.client.post(
            reverse("neo_editor:neojson_save", args=[self.vf.pk]),
            {"content": bad},
        )
        self.assertEqual(resp.status_code, 400)
