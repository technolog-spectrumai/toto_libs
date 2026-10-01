"""No Office name for what the knowledge graph saves into the vault (2026-10-01).

The NeoJSON export dialog ("Save graph as NeoJSON") stored the graph under
whatever name it was given, so "graph.docx" put the graph's JSON in the vault
under Word's extension — listed so, and named so by the API's download —
and an analysis's File Title did the same for its JSON, YAML or CSV. Both
ask the vault's own rule now, `upload_refusal`, and say its sentence: the
dialog's door, the analysis page's door, and the analysis task itself, which
a run started anywhere else reaches.

Run under the graph's own harness (no host here installs the app):

    manage.py test toto.ravioli.tests.test_office_names --settings=toto.ravioli.testing.settings
"""

from __future__ import annotations

import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.ravioli import views
from toto.ravioli.models import CypherQuery, CypherQueryResult
from toto.vault.models import (Bucket, OFFICE_EXTENSIONS, VaultFile,
                               office_refusal_sentence)

User = get_user_model()

NODES = [{"id": "n1", "labels": ["Person"], "props": {"name": "Alice"}},
         {"id": "n2", "labels": ["Person"], "props": {"name": "Bob"}}]
EDGES = [{"id": "e1", "type": "KNOWS", "start": "n1", "end": "n2", "props": {}}]


class _Graph(TestCase):
    def setUp(self):
        # The vault's bytes are real files; somewhere disposable, one
        # directory per test.
        media = tempfile.mkdtemp(prefix="ravioli-office-test-")
        media_override = override_settings(MEDIA_ROOT=media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.addCleanup(shutil.rmtree, media, ignore_errors=True)

        self.root = User.objects.create_superuser("root", "root@example.com", "pw")
        self.bucket = Bucket.objects.create(name="Graphs", slug="graphs",
                                            owner=self.root, storage_backend="local")
        self.query = CypherQuery.objects.create(name="Who knows whom",
                                                query="MATCH (n) RETURN n")
        # The export reads the cached result: Neo4j is never asked.
        CypherQueryResult.objects.create(query=self.query, result_nodes=NODES,
                                         result_edges=EDGES)
        self.client.force_login(self.root)


class ExportNameTests(_Graph):
    """The "Save graph as NeoJSON" dialog's door."""

    def _export(self, name):
        return self.client.post(
            reverse("ravioli:export_query_neojson", args=[self.query.pk]),
            {"bucket_id": self.bucket.pk, "name": name})

    def test_every_office_extension_is_refused_with_the_vault_sentence(self):
        for ext in sorted(OFFICE_EXTENSIONS):
            for name in (f"graph{ext}", f"Graph{ext.upper()}"):
                with self.subTest(name=name):
                    response = self._export(name)
                    self.assertEqual(response.status_code, 400, response.content)
                    self.assertEqual(response.json()["error"], office_refusal_sentence())
        self.assertFalse(VaultFile.objects.exists())

    def test_a_json_name_is_saved_as_neojson(self):
        response = self._export("graph.json")
        self.assertEqual(response.status_code, 200, response.content)
        saved = VaultFile.objects.get(pk=response.json()["vault_file_id"])
        self.assertEqual((saved.title, saved.file_type, saved.bucket_id),
                         ("graph.json", "neojson", self.bucket.pk))
        self.assertEqual(response.json()["editor_url"],
                         reverse("neo_editor:neojson_editor", args=[saved.pk]))

    def test_no_name_still_takes_the_query_s_name(self):
        response = self._export("")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["filename"], "who-knows-whom.json")

    def test_an_office_word_inside_the_name_is_not_an_extension(self):
        response = self._export("graph.docx.json")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["filename"], "graph.docx.json")

    @override_settings(VAULT_REFUSED_FILE_TYPES={"neojson"})
    def test_a_type_the_host_refuses_is_refused_here_too(self):
        response = self._export("graph.json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(response.json()["error"],
                         "This host does not accept neojson files.")
        self.assertFalse(VaultFile.objects.exists())


@mock.patch.object(views, "celery_available", return_value=True)
class AnalysisTitleDoorTests(_Graph):
    """The Graph Analysis page's door refuses before a run is queued."""

    def _start(self, title, fmt="csv"):
        return self.client.post(reverse("ravioli:start_graph_analysis"), {
            "query_id": self.query.pk,
            "workflow_slug": "graph-analysis-centrality",
            "bucket_id": self.bucket.pk,
            "format": fmt,
            "title": title,
        })

    def test_an_office_title_is_refused_and_nothing_is_queued(self, _celery):
        with mock.patch.object(views, "_trigger_workflow") as trigger:
            for title, fmt in (("results.xlsx", "csv"), ("Report.DOCX", "json"),
                               ("graph.pptx", "neojson"), ("old.xls", "yaml")):
                with self.subTest(title=title):
                    response = self._start(title, fmt)
                    self.assertEqual(response.status_code, 400, response.content)
                    self.assertIn(office_refusal_sentence(), response.json()["error"])
        trigger.assert_not_called()

    def test_a_plain_title_is_queued_with_it(self, _celery):
        with mock.patch.object(views, "_trigger_workflow",
                               return_value=mock.Mock(pk=7)) as trigger:
            response = self._start("results.csv")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {"run_id": 7})
        trigger.assert_called_once()
        self.assertEqual(trigger.call_args.kwargs["input_data"]["data"]["title"],
                         "results.csv")

    @override_settings(VAULT_REFUSED_FILE_TYPES={"csv"})
    def test_a_format_the_host_refuses_is_refused_at_the_door(self, _celery):
        with mock.patch.object(views, "_trigger_workflow") as trigger:
            response = self._start("results")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("This host does not accept csv files.", response.json()["error"])
        trigger.assert_not_called()


class AnalysisTitleTaskTests(_Graph):
    """The task that writes the analysis's file: a run started anywhere
    else than the page reaches it without the door's check."""

    def _save(self, **data):
        from toto.ravioli.predefined_tasks import ravioli_save_graph_analysis_output

        payload = {"bucket_id": self.bucket.pk, "owner_id": self.root.pk,
                   "query_id": self.query.pk, "format": "csv"}
        payload.update(data)
        return ravioli_save_graph_analysis_output({"data": payload})

    def test_an_office_title_fails_the_task_with_the_vault_sentence(self):
        for title, fmt in (("results.xlsx", "csv"), ("Report.DOCX", "json"),
                           ("graph.pptx", "neojson"), ("old.xls", "yaml")):
            with self.subTest(title=title, fmt=fmt):
                with self.assertRaisesMessage(ValueError, office_refusal_sentence()):
                    self._save(title=title, format=fmt)
        self.assertFalse(VaultFile.objects.exists())

    def test_a_plain_title_is_saved(self):
        out = self._save(title="Who knows whom", format="csv")
        saved = VaultFile.objects.get(pk=out["data"]["vault_file_id"])
        self.assertEqual((saved.title, saved.file_type), ("Who knows whom", "csv"))

    def test_no_title_is_still_graph_analysis(self):
        out = self._save(format="neojson")
        saved = VaultFile.objects.get(pk=out["data"]["vault_file_id"])
        self.assertEqual((saved.title, saved.file_type), ("Graph Analysis", "neojson"))

    @override_settings(VAULT_REFUSED_FILE_TYPES={"yaml"})
    def test_a_type_the_host_refuses_fails_the_task(self):
        with self.assertRaisesMessage(ValueError, "This host does not accept yaml files."):
            self._save(title="results", format="yaml")
        self.assertFalse(VaultFile.objects.exists())
