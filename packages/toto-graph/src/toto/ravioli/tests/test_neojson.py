import json

from django.contrib.auth.models import User
from django.test import Client, SimpleTestCase, TestCase
from django.urls import reverse

from toto.ravioli import neojson


def _raw_nodes():
    """Mirror Neo4jClient.extract_graph() node output."""
    return [
        {"id": "n1", "labels": ["Person"], "props": {"name": "Alice", "age": 30}},
        {"id": "n2", "labels": ["Person", "Employee"], "props": {"name": "Bob"}},
    ]


def _raw_edges():
    """Mirror Neo4jClient.extract_graph() edge output."""
    return [
        {"id": "rel-1", "type": "KNOWS", "start": "n1", "end": "n2", "props": {"since": 2020}},
    ]


class NeoJsonSerializerTests(SimpleTestCase):
    """Pure serializer logic — no DB."""

    def test_from_ravioli_shape_and_metadata(self):
        g = neojson.from_ravioli(_raw_nodes(), _raw_edges())
        d = g.to_dict()

        self.assertEqual(d["neojson"], neojson.FORMAT_VERSION)
        self.assertEqual(d["type"], "Graph")
        self.assertTrue(d["directed"])
        self.assertEqual(len(d["nodes"]), 2)
        self.assertEqual(len(d["relationships"]), 1)

        node = d["nodes"][0]
        self.assertEqual(node["type"], "Node")
        self.assertEqual(node["id"], "n1")
        self.assertEqual(node["labels"], ["Person"])
        self.assertEqual(node["properties"]["name"], "Alice")

        rel = d["relationships"][0]
        self.assertEqual(rel["type"], "Relationship")
        self.assertEqual(rel["relType"], "KNOWS")
        self.assertEqual(rel["start"], "n1")
        self.assertEqual(rel["end"], "n2")
        self.assertEqual(rel["properties"]["since"], 2020)

        # Auto-computed metadata reflects actual content.
        meta = d["metadata"]
        self.assertEqual(meta["node_count"], 2)
        self.assertEqual(meta["relationship_count"], 1)
        self.assertEqual(meta["labels"], ["Employee", "Person"])
        self.assertEqual(meta["relationship_types"], ["KNOWS"])

    def test_round_trip(self):
        g = neojson.from_ravioli(_raw_nodes(), _raw_edges())
        text = neojson.dumps(g)
        json.loads(text)
        g2 = neojson.loads(text)
        self.assertEqual(g2.to_dict(), g.to_dict())

    def test_metadata_passthrough_preserved(self):
        g = neojson.from_ravioli(
            _raw_nodes(), _raw_edges(), metadata={"source": "ravioli:cypher_query/7"}
        )
        d = neojson.loads(neojson.dumps(g)).to_dict()
        self.assertEqual(d["metadata"]["source"], "ravioli:cypher_query/7")
        self.assertEqual(d["metadata"]["node_count"], 2)

    def test_loads_empty_returns_empty_graph(self):
        g = neojson.loads("")
        self.assertEqual(g.nodes, [])
        self.assertEqual(g.relationships, [])

    def test_loads_invalid_raises(self):
        with self.assertRaises(neojson.NeoJsonParseError):
            neojson.loads("{not json")

    def test_loads_wrong_root_type_raises(self):
        with self.assertRaises(neojson.NeoJsonParseError):
            neojson.loads('{"neojson": "1.0", "type": "FeatureCollection"}')

    def test_new_graph_is_empty_and_valid(self):
        g = neojson.new_graph()
        self.assertEqual(neojson.validate(g), [])
        self.assertEqual(g.to_dict()["nodes"], [])

    def test_validate_ok(self):
        g = neojson.from_ravioli(_raw_nodes(), _raw_edges())
        self.assertEqual(neojson.validate(g), [])
        self.assertEqual(neojson.validate(neojson.dumps(g)), [])

    def test_validate_dangling_endpoint(self):
        doc = {
            "neojson": "1.0",
            "type": "Graph",
            "nodes": [{"type": "Node", "id": "n1"}],
            "relationships": [
                {"type": "Relationship", "relType": "KNOWS", "start": "n1", "end": "ghost"}
            ],
        }
        errors = neojson.validate(doc)
        self.assertTrue(any("ghost" in e for e in errors), errors)

    def test_validate_missing_version_and_bad_type(self):
        errors = neojson.validate({"type": "Nope", "nodes": [], "relationships": []})
        self.assertTrue(any("neojson" in e for e in errors), errors)
        self.assertTrue(any("Graph" in e for e in errors), errors)

    def test_networkx_round_trip(self):
        try:
            import networkx  # noqa: F401
        except ImportError:
            self.skipTest("networkx not installed")

        g = neojson.from_ravioli(_raw_nodes(), _raw_edges())
        G = neojson.to_networkx(g)
        self.assertEqual(G.number_of_nodes(), 2)
        self.assertEqual(G.number_of_edges(), 1)

        g2 = neojson.from_networkx(G)
        self.assertEqual(g2.to_dict()["nodes"], g.to_dict()["nodes"])
        self.assertEqual(g2.to_dict()["relationships"], g.to_dict()["relationships"])


class NeoJsonVaultTypeTests(SimpleTestCase):
    """The vault recognizes .neojson files."""

    def test_detect_type_by_extension(self):
        from toto.vault.models import VaultFile
        self.assertEqual(VaultFile.detect_type("", "graph.neojson"), "neojson")

    def test_detect_type_by_mime(self):
        from toto.vault.models import VaultFile
        self.assertEqual(VaultFile.detect_type(neojson.MIME), "neojson")

    def test_plain_json_still_detected_as_json(self):
        from toto.vault.models import VaultFile
        self.assertEqual(VaultFile.detect_type("application/json", "data.json"), "json")


class NeoJsonSaveTaskTests(TestCase):
    """The graph-analysis save task can emit a .neojson VaultFile."""

    def setUp(self):
        from toto.vault.models import Bucket

        self.user = User.objects.create_user("neojson_user", password="pw")
        self.bucket = Bucket.objects.create(
            name="NeoJSON Bucket", owner=self.user, slug="neojson-bucket"
        )
        from toto.ravioli.models import CypherQuery, CypherQueryResult

        self.query = CypherQuery.objects.create(name="People", query="MATCH (n) RETURN n")
        CypherQueryResult.objects.create(
            query=self.query,
            result_nodes=_raw_nodes(),
            result_edges=_raw_edges(),
        )

    def test_save_creates_neojson_vault_file(self):
        from toto.ravioli.predefined_tasks import ravioli_save_graph_analysis_output
        from toto.vault.models import VaultFile

        result = ravioli_save_graph_analysis_output({
            "data": {
                "query_id": self.query.pk,
                "owner_id": self.user.pk,
                "bucket_id": self.bucket.pk,
                "format": "neojson",
                "title": "People Graph",
            }
        })

        vf = VaultFile.objects.get(pk=result["data"]["vault_file_id"])
        self.assertEqual(vf.file_type, "neojson")
        self.assertTrue(vf.file.name.endswith(".neojson"))

        with vf.file.open("rb") as fh:
            graph = neojson.loads(fh.read().decode("utf-8"))

        self.assertEqual(neojson.validate(graph), [])
        d = graph.to_dict()
        self.assertEqual(d["metadata"]["node_count"], 2)
        self.assertEqual(d["metadata"]["relationship_count"], 1)
        self.assertEqual({n["id"] for n in d["nodes"]}, {"n1", "n2"})
        self.assertEqual(d["relationships"][0]["relType"], "KNOWS")
        self.assertEqual(d["metadata"]["source"], f"ravioli:cypher_query/{self.query.pk}")


class NeoJsonExportViewTests(TestCase):
    """export_query_neojson_view turns a cached query result into a vault file (one-click)."""

    def setUp(self):
        from toto.ravioli.models import CypherQuery, CypherQueryResult
        from toto.vault.models import Bucket

        self.user = User.objects.create_superuser("neojson_super", password="pw")
        # Not slug 'general' — exercises the "caller-owned" auto-pick fallback.
        self.bucket = Bucket.objects.create(name="Export Bucket", owner=self.user, slug="export-bucket")
        self.query = CypherQuery.objects.create(name="People", query="MATCH (n) RETURN n")
        CypherQueryResult.objects.create(
            query=self.query, result_nodes=_raw_nodes(), result_edges=_raw_edges(),
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_export_auto_saves_json_named_neojson_file(self):
        from toto.vault.models import VaultFile

        url = reverse("ravioli:export_query_neojson", args=[self.query.pk])
        resp = self.client.post(url)   # one-click: no bucket, no title
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["node_count"], 2)
        self.assertEqual(data["relationship_count"], 1)
        self.assertEqual(data["filename"], "people.json")
        self.assertIn("editor_url", data)

        vf = VaultFile.objects.get(pk=data["vault_file_id"])
        self.assertEqual(vf.bucket, self.bucket)          # auto-picked owned bucket
        self.assertEqual(vf.file_type, "neojson")          # stored as neojson…
        self.assertTrue(vf.title.endswith(".json"))        # …but named .json
        self.assertTrue(vf.file.name.endswith(".json"))
        with vf.file.open("rb") as fh:
            graph = neojson.loads(fh.read().decode("utf-8"))
        self.assertEqual(neojson.validate(graph), [])
        self.assertEqual({n.id for n in graph.nodes}, {"n1", "n2"})

    def test_export_with_chosen_bucket_and_name(self):
        from toto.vault.models import Bucket, VaultFile

        other = Bucket.objects.create(name="Other", owner=self.user, slug="other-bucket")
        url = reverse("ravioli:export_query_neojson", args=[self.query.pk])
        resp = self.client.post(url, {"bucket_id": other.pk, "name": "My Graph.json"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["filename"], "My Graph.json")
        self.assertEqual(data["bucket"], "Other")

        vf = VaultFile.objects.get(pk=data["vault_file_id"])
        self.assertEqual(vf.bucket, other)
        self.assertEqual(vf.file_type, "neojson")
        self.assertTrue(vf.file.name.endswith(".json"))

    def test_export_unique_key_on_repeat(self):
        url = reverse("ravioli:export_query_neojson", args=[self.query.pk])
        self.assertEqual(self.client.post(url).status_code, 200)
        # A second export of the same query must not collide on the bucket+key.
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_export_no_bucket_available(self):
        from toto.vault.models import Bucket

        Bucket.objects.all().delete()
        resp = self.client.post(reverse("ravioli:export_query_neojson", args=[self.query.pk]))
        self.assertEqual(resp.status_code, 400)

