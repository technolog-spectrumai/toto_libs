import json

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings

from toto.ravioli.connection import connection_uris
from toto.sql_neo4j_sync.planner import ProjectionPlanApplier, prop_changes, summarize_diff
from toto.sql_neo4j_sync.projection import _get_value


class FakeNeo4jClient:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params or {}))
        return []


class ProjectionPlannerTests(SimpleTestCase):
    def test_neo4j_uri_adds_local_debug_fallback_for_docker_host(self):
        self.assertEqual(
            connection_uris("bolt://neo4j:7687"),
            ["bolt://127.0.0.1:7687", "bolt://neo4j:7687"],
        )

    @override_settings(RAVIOLI_NEO4J_LOCAL_FALLBACK=False)
    def test_neo4j_uri_fallback_can_be_disabled(self):
        self.assertEqual(
            connection_uris("bolt://neo4j:7687"),
            ["bolt://neo4j:7687"],
        )

    def test_prop_changes_compares_expected_fields_only(self):
        changes = prop_changes(
            {"title": "New", "body": "Same"},
            {"title": "Old", "body": "Same", "extra": "Ignored"},
        )

        self.assertEqual(
            changes,
            {"title": {"from": "Old", "to": "New"}},
        )

    def test_default_dict_transform_serializes_to_json_property(self):
        class Obj:
            properties = {"rating": 3, "kind": "question", "status": "open"}

        value = _get_value(
            Obj(),
            {"source": "properties", "transform": "default_dict"},
        )

        self.assertEqual(
            value,
            '{"kind": "question", "rating": 3, "status": "open"}',
        )

    def test_summarize_diff_counts_non_ignored_changes(self):
        diff = {
            "nodes": {
                "create": [{"uuid": "1"}],
                "update": [{"uuid": "2"}],
                "delete": [],
                "ignored": [{"uuid": "3"}],
            },
            "relationships": {
                "create": [],
                "update": [{"key": "r1"}],
                "delete": [{"key": "r2"}],
                "ignored": [{"key": "r3"}],
            },
        }

        summary = summarize_diff(diff)

        self.assertEqual(summary["nodes"]["ignored"], 1)
        self.assertEqual(summary["relationships"]["delete"], 1)
        self.assertEqual(summary["total_changes"], 4)

    def test_applier_runs_node_and_relationship_operations(self):
        client = FakeNeo4jClient()
        applier = ProjectionPlanApplier(client)
        diff = {
            "nodes": {
                "create": [
                    {"label": "IdeaBox", "uuid": "box-1", "props": {"title": "A"}},
                ],
                "update": [],
                "delete": [
                    {"label": "IdeaBox", "uuid": "box-2"},
                ],
                "ignored": [],
            },
            "relationships": {
                "create": [
                    {
                        "kind": "direct",
                        "from_label": "IdeaBox",
                        "from_uuid": "box-1",
                        "relation": "HAS_CATEGORY",
                        "to_label": "IdeaCategory",
                        "to_uuid": "cat-1",
                        "props": {},
                    }
                ],
                "update": [],
                "delete": [],
                "ignored": [],
            },
        }

        applier.apply(diff)

        self.assertEqual(len(client.calls), 3)
        self.assertIn("MERGE (n:IdeaBox", client.calls[0][0])
        self.assertIn("MERGE (a)-[r:HAS_CATEGORY]->(b)", client.calls[1][0])
        self.assertIn("DETACH DELETE", client.calls[2][0])

    def test_applier_serializes_dict_props_from_existing_plan(self):
        client = FakeNeo4jClient()
        applier = ProjectionPlanApplier(client)
        diff = {
            "nodes": {
                "create": [
                    {
                        "label": "IdeaBox",
                        "uuid": "box-1",
                        "props": {
                            "properties": {
                                "rating": 3,
                                "kind": "question",
                                "status": "open",
                            },
                        },
                    },
                ],
                "update": [],
                "delete": [],
                "ignored": [],
            },
            "relationships": {
                "create": [],
                "update": [],
                "delete": [],
                "ignored": [],
            },
        }

        applier.apply(diff)

        self.assertEqual(
            client.calls[0][1]["props"]["properties"],
            '{"kind": "question", "rating": 3, "status": "open"}',
        )


# ---------------------------------------------------------------------------
# Graph Analysis helper tests
# ---------------------------------------------------------------------------

class GraphAnalysisHelpersTests(SimpleTestCase):
    """Unit tests for graph_analysis.py helpers — no DB, no networkx required."""

    def _raw_nodes(self):
        return [
            {"id": "n1", "labels": ["Person"], "props": {"name": "Alice"}},
            {"id": "n2", "labels": ["Person"], "props": {"name": "Bob"}},
        ]

    def _raw_edges(self):
        return [
            {"id": "e1", "start": "n1", "end": "n2", "type": "KNOWS", "props": {}},
        ]

    def test_build_networkx_graph_nodes_and_edges(self):
        try:
            import networkx as nx  # noqa: F401
        except ImportError:
            self.skipTest("networkx not installed")

        from toto.ravioli.graph_analysis import build_networkx_graph

        G = build_networkx_graph(self._raw_nodes(), self._raw_edges())

        self.assertEqual(G.number_of_nodes(), 2)
        self.assertEqual(G.number_of_edges(), 1)
        self.assertIn("n1", G.nodes)
        self.assertEqual(G.nodes["n1"]["name"], "Alice")

    def test_serialize_graph_is_json_safe(self):
        try:
            import networkx as nx
        except ImportError:
            self.skipTest("networkx not installed")

        from toto.ravioli.graph_analysis import build_networkx_graph, serialize_graph

        G = build_networkx_graph(self._raw_nodes(), self._raw_edges())
        data = serialize_graph(G)

        # Must be JSON-serialisable without error
        serialised = json.dumps(data)
        self.assertIn("nodes", json.loads(serialised))

    def test_serialize_output_json(self):
        from toto.ravioli.graph_analysis import serialize_output

        payload = {"node_count": 5, "density": 0.25}
        content, mime, ext = serialize_output(payload, "json")

        self.assertEqual(ext, ".json")
        self.assertIn("application/json", mime)
        parsed = json.loads(content.decode())
        self.assertEqual(parsed["node_count"], 5)

    def test_serialize_output_yaml(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")

        from toto.ravioli.graph_analysis import serialize_output

        payload = {"key": "value"}
        content, mime, ext = serialize_output(payload, "yaml")

        self.assertEqual(ext, ".yaml")
        self.assertIn("yaml", mime)
        self.assertIn(b"key: value", content)

    def test_serialize_output_csv_dict(self):
        from toto.ravioli.graph_analysis import serialize_output

        payload = {"alpha": 1, "beta": 2}
        content, mime, ext = serialize_output(payload, "csv")

        self.assertEqual(ext, ".csv")
        self.assertIn(b"key,value", content)
        self.assertIn(b"alpha", content)

    def test_serialize_output_csv_list_of_dicts(self):
        from toto.ravioli.graph_analysis import serialize_output

        payload = [{"name": "Alice", "score": 9}, {"name": "Bob", "score": 7}]
        content, mime, ext = serialize_output(payload, "csv")

        lines = content.decode().splitlines()
        self.assertEqual(lines[0], "name,score")
        self.assertIn("Alice", lines[1])

    def test_serialize_output_unsupported_format_raises(self):
        from toto.ravioli.graph_analysis import serialize_output

        with self.assertRaises(ValueError):
            serialize_output({}, "xml")


class GraphAnalysisVaultSaveTests(TestCase):
    """Integration tests for ravioli_save_graph_analysis_output."""

    def setUp(self):
        self.user = User.objects.create_user("test_ga_user", password="pw")
        from toto.vault.models import Bucket
        self.bucket = Bucket.objects.create(
            name="GA Test Bucket",
            owner=self.user,
            slug="ga-test-bucket",
        )

    def test_save_creates_vault_file(self):
        from toto.ravioli.predefined_tasks import ravioli_save_graph_analysis_output

        result = ravioli_save_graph_analysis_output({
            "data": {
                "query_id": 1,
                "owner_id": self.user.pk,
                "bucket_id": self.bucket.pk,
                "format": "json",
                "title": "Test Analysis",
                "graph_summary": {"node_count": 3, "edge_count": 2},
            }
        })

        data = result["data"]
        self.assertIn("vault_file_id", data)
        self.assertIsNotNone(data["vault_file_id"])

        from toto.vault.models import VaultFile
        vf = VaultFile.objects.get(pk=data["vault_file_id"])
        self.assertEqual(vf.owner, self.user)
        self.assertEqual(vf.bucket, self.bucket)
        self.assertEqual(vf.title, "Test Analysis")
        self.assertEqual(vf.file_type, "json")

    def test_save_missing_bucket_raises(self):
        from toto.ravioli.predefined_tasks import ravioli_save_graph_analysis_output

        with self.assertRaises(ValueError, msg="requires bucket_id"):
            ravioli_save_graph_analysis_output({"data": {"owner_id": self.user.pk, "format": "json"}})

    def test_save_invalid_bucket_raises(self):
        from toto.ravioli.predefined_tasks import ravioli_save_graph_analysis_output

        with self.assertRaises(ValueError):
            ravioli_save_graph_analysis_output({
                "data": {"owner_id": self.user.pk, "bucket_id": 99999, "format": "json"}
            })

    def test_save_unsupported_format_raises(self):
        from toto.ravioli.predefined_tasks import ravioli_save_graph_analysis_output

        with self.assertRaises(ValueError):
            ravioli_save_graph_analysis_output({
                "data": {
                    "owner_id": self.user.pk,
                    "bucket_id": self.bucket.pk,
                    "format": "pdf",
                }
            })


class GraphAnalysisViewTests(TestCase):
    """View-level tests for the graph analysis endpoints."""

    def setUp(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="Test", author="T", publication_year=2024, active=True)
        self.superuser = User.objects.create_superuser("ga_super", password="pw")
        self.client.login(username="ga_super", password="pw")

    def test_get_graph_analysis_view_renders(self):
        response = self.client.get("/ravioli/graph-analysis/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Graph Analysis")

    def test_start_graph_analysis_missing_fields_returns_400(self):
        response = self.client.post("/ravioli/graph-analysis/start/", {})
        self.assertEqual(response.status_code, 400)
        data = json.loads(response.content)
        self.assertIn("error", data)

    def test_start_graph_analysis_invalid_format_returns_400(self):
        from toto.ravioli.models import CypherQuery
        from toto.vault.models import Bucket

        q = CypherQuery.objects.create(name="T", query="MATCH (n) RETURN n")
        b = Bucket.objects.create(name="TestBkt", owner=self.superuser, slug="testbkt")

        response = self.client.post("/ravioli/graph-analysis/start/", {
            "query_id": q.pk,
            "workflow_slug": "graph-analysis-basic",
            "bucket_id": b.pk,
            "format": "pdf",
        })
        self.assertEqual(response.status_code, 400)

    def test_status_endpoint_returns_json(self):
        from toto.workflows.models import Workflow, WorkflowRun

        wf = Workflow.objects.create(name="GA Test WF", slug="graph-analysis-status-test")
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.PENDING)

        response = self.client.get(f"/ravioli/graph-analysis/status/{run.pk}/")
        self.assertEqual(response.status_code, 200)
        data = json.loads(response.content)
        self.assertIn("status", data)
        self.assertIn("percent", data)

    def test_status_percent_based_on_node_runs(self):
        from toto.workflows.models import Workflow, WorkflowNode, WorkflowRun, WorkflowNodeRun

        wf = Workflow.objects.create(name="GA Pct WF", slug="graph-analysis-pct-test")
        n1 = WorkflowNode.objects.create(
            workflow=wf, node_type=WorkflowNode.PREDEFINED_TASK,
            task_name="ravioli_prepare_graph_analysis",
        )
        n2 = WorkflowNode.objects.create(
            workflow=wf, node_type=WorkflowNode.PREDEFINED_TASK,
            task_name="ravioli_save_graph_analysis_output",
        )
        run = WorkflowRun.objects.create(workflow=wf, status=WorkflowRun.RUNNING)
        WorkflowNodeRun.objects.create(
            workflow_run=run, node=n1, status=WorkflowNodeRun.COMPLETED
        )
        WorkflowNodeRun.objects.create(
            workflow_run=run, node=n2, status=WorkflowNodeRun.PENDING
        )

        response = self.client.get(f"/ravioli/graph-analysis/status/{run.pk}/")
        data = json.loads(response.content)
        self.assertEqual(data["completed_nodes"], 1)
        self.assertEqual(data["total_nodes"], 2)
        self.assertEqual(data["percent"], 50)


class GraphAnalysisIngressTests(TestCase):
    """Test that ingress_ravioli creates the graph-analysis-basic workflow idempotently."""

    def _run_ingress(self):
        from django.core.management import call_command
        from io import StringIO
        call_command("ingress_ravioli", stdout=StringIO(), stderr=StringIO())

    def test_ingress_creates_workflow(self):
        from toto.workflows.models import Workflow

        self._run_ingress()

        wf = Workflow.objects.filter(slug="graph-analysis-basic").first()
        self.assertIsNotNone(wf)
        self.assertEqual(wf.nodes.count(), 3)

    def test_ingress_is_idempotent(self):
        from toto.workflows.models import Workflow

        self._run_ingress()
        self._run_ingress()

        count = Workflow.objects.filter(slug="graph-analysis-basic").count()
        self.assertEqual(count, 1)
