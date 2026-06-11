"""Tests for ravioli.graph_export — the per-object 1-hop diff/preview/apply engine.

Uses Django's auth ``User --MEMBER_OF--> Group`` (a real M2M) as a fixture-free
graph, and a scripted in-memory Neo4j client so the read-diff-write path can be
exercised without a live database.
"""

import re

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase, override_settings

from toto.ravioli.graph_export import GraphExporter, content_checksum, is_app_excluded


# A self-contained graph config: User --MEMBER_OF--> Group.
AUTH_CONFIGS = [
    {
        "app": "auth",
        "nodes": [
            {
                "label": "TUser",
                "model": "django.contrib.auth.models.User",
                "uuid_field": "username",
                "fields": {"name": "username"},
            },
            {
                "label": "TGroup",
                "model": "django.contrib.auth.models.Group",
                "uuid_field": "name",
                "fields": {"name": "name"},
            },
        ],
        "links": [
            {
                "from_label": "TUser",
                "to_label": "TGroup",
                "relation": "MEMBER_OF",
                "source": "groups",
                "cardinality": "many",
            },
        ],
    }
]


class ScriptedClient:
    """Minimal fake: serves seeded node reads, records every query.

    diff_slice/apply read node state *before* writing, so seeding the canonical
    node is enough to drive the create/changed/unchanged branches; writes are
    only recorded (and asserted on).
    """

    def __init__(self, nodes=None):
        self.nodes = dict(nodes or {})       # {(label, uuid): props}
        self.calls = []

    def run_cypher(self, query, params=None):
        params = params or {}
        self.calls.append((query, params))
        m = re.search(r"\(n:(\w+) \{uuid: \$uuid\}\) RETURN properties", query)
        if m:
            props = self.nodes.get((m.group(1), params.get("uuid")))
            return [{"props": props}] if props is not None else []
        if "RETURN r LIMIT 1" in query:
            return []                         # treat desired edges as new
        return []

    def close(self):
        pass


class ChecksumTests(SimpleTestCase):
    def test_checksum_ignores_reserved_props(self):
        base = content_checksum({"name": "X"})
        decorated = content_checksum({
            "name": "X",
            "_checksum": "zzz",
            "uuid": "1",
            "_prev_uuid": "0",
            "_archived_at": 123,
            "_historical": True,
        })
        self.assertEqual(base, decorated)

    def test_checksum_changes_with_content(self):
        self.assertNotEqual(content_checksum({"name": "X"}), content_checksum({"name": "Y"}))


class ExclusionTests(SimpleTestCase):
    def test_default_excluded_apps(self):
        for app in ("workflows", "fileservices", "vault"):
            self.assertTrue(is_app_excluded(app))
        self.assertFalse(is_app_excluded("events"))

    def test_excluded_app_config_is_not_registered(self):
        cfg = [{
            "app": "vault",
            "nodes": [{
                "label": "X",
                "model": "django.contrib.auth.models.User",
                "uuid_field": "username",
                "fields": {},
            }],
            "links": [],
        }]
        self.assertIsNone(GraphExporter(None, cfg).label_for_model(User))


class SliceAndPreviewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.g1 = Group.objects.create(name="editors")
        self.g2 = Group.objects.create(name="reviewers")
        self.user.groups.add(self.g1, self.g2)

    def test_desired_slice_is_root_plus_one_hop(self):
        root, nodes, edges = GraphExporter(ScriptedClient(), AUTH_CONFIGS).desired_slice(
            "TUser", self.user
        )
        self.assertEqual(root["uuid"], "alice")
        self.assertEqual(len(nodes), 3)        # user + 2 groups
        self.assertEqual(len(edges), 2)

    def test_preview_all_new_when_graph_empty(self):
        cy_nodes, _cy_edges, summary = GraphExporter(
            ScriptedClient(), AUTH_CONFIGS
        ).preview("TUser", self.user)
        self.assertEqual(summary["new_nodes"], 3)
        self.assertEqual(summary["new_edges"], 2)
        root = next(n for n in cy_nodes if n["is_root"])
        self.assertEqual(root["status"], "new")

    def test_preview_marks_existing_node_unchanged(self):
        exp = GraphExporter(ScriptedClient(), AUTH_CONFIGS)
        _root, nodes, _edges = exp.desired_slice("TUser", self.user)
        user_node = nodes[("TUser", "alice")]
        client = ScriptedClient({
            ("TUser", "alice"): {**user_node["props"], "_checksum": user_node["checksum"]},
        })
        _cy_nodes, _cy_edges, summary = GraphExporter(client, AUTH_CONFIGS).preview(
            "TUser", self.user
        )
        self.assertEqual(summary["existing_nodes"], 1)   # the user
        self.assertEqual(summary["new_nodes"], 2)        # the two groups


class ApplyTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.user.groups.add(
            Group.objects.create(name="editors"),
            Group.objects.create(name="reviewers"),
        )

    def _desired_user_node(self):
        exp = GraphExporter(ScriptedClient(), AUTH_CONFIGS)
        _root, nodes, _edges = exp.desired_slice("TUser", self.user)
        return nodes[("TUser", "alice")]

    def test_unchanged_checksum_skips_history(self):
        node = self._desired_user_node()
        client = ScriptedClient({
            ("TUser", "alice"): {**node["props"], "_checksum": node["checksum"]},
        })
        result = GraphExporter(client, AUTH_CONFIGS).apply("TUser", self.user)

        self.assertEqual(result["unchanged"], 1)         # the user node
        self.assertEqual(result["created"], 2)           # the two groups
        self.assertFalse(any(q.startswith("CREATE (h:TUser") for q, _ in client.calls))

    def test_changed_checksum_archives_and_updates(self):
        client = ScriptedClient({
            ("TUser", "alice"): {"name": "OLD", "_checksum": "deadbeef"},
        })
        result = GraphExporter(client, AUTH_CONFIGS).apply("TUser", self.user)

        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["archived"], 1)

        creates = [p for q, p in client.calls if q.startswith("CREATE (h:TUser")]
        self.assertEqual(len(creates), 1)
        snapshot = creates[0]["props"]
        self.assertEqual(snapshot["_prev_uuid"], "alice")
        self.assertNotEqual(snapshot["uuid"], "alice")
        self.assertTrue(snapshot["_historical"])

        self.assertTrue(any("MERGE (n)-[:_HISTORICAL]->(h)" in q for q, _ in client.calls))
        # History pruning is disabled for now — no DETACH DELETE of old versions.
        self.assertFalse(any("DETACH DELETE h" in q for q, _ in client.calls))

    def test_apply_merges_edges_and_clears_stale(self):
        client = ScriptedClient()
        GraphExporter(client, AUTH_CONFIGS).apply("TUser", self.user)
        merges = [q for q, _ in client.calls if "MERGE (a)-[:MEMBER_OF]->(b)" in q]
        self.assertEqual(len(merges), 2)
        self.assertTrue(
            any("[r:MEMBER_OF]->(b) WHERE NOT b.uuid IN $keep DELETE r" in q
                for q, _ in client.calls)
        )


class ExportViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("carol", password="pw")

    def test_preview_requires_login(self):
        resp = self.client.get("/ravioli/export/events/scheduledevent/abc/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url.lower())

    @override_settings(RAVIOLI_ENABLED=False)
    def test_preview_disabled_redirects(self):
        self.client.login(username="carol", password="pw")
        resp = self.client.get(
            "/ravioli/export/events/scheduledevent/abc/", HTTP_REFERER="/events/"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/events/")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_preview_excluded_app_redirects(self):
        self.client.login(username="carol", password="pw")
        resp = self.client.get(
            "/ravioli/export/vault/vaultfile/abc/", HTTP_REFERER="/vault/"
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/vault/")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_preview_unmapped_model_redirects(self):
        self.client.login(username="carol", password="pw")
        resp = self.client.get("/ravioli/export/auth/user/carol/", HTTP_REFERER="/x/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/x/")

    def test_apply_requires_post(self):
        self.client.login(username="carol", password="pw")
        resp = self.client.get("/ravioli/export/events/scheduledevent/abc/apply/")
        self.assertEqual(resp.status_code, 405)


class PlanViewTests(TestCase):
    def _ready_plan(self, total=0):
        from toto.sql_neo4j_sync.models import GraphProjectionPlan
        return GraphProjectionPlan.objects.create(
            status=GraphProjectionPlan.STATUS_READY,
            summary={"total_changes": total},
            diff={},
        )

    # -- sync plan --
    def test_sync_plan_requires_superuser(self):
        User.objects.create_user("plain2", password="pw")
        self.client.login(username="plain2", password="pw")
        self.assertEqual(self.client.post("/ravioli/graph-sync/plan/").status_code, 302)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_sync_plan_disabled_returns_503(self):
        User.objects.create_superuser("admin3", email="a@b.c", password="pw")
        self.client.login(username="admin3", password="pw")
        resp = self.client.post("/ravioli/graph-sync/plan/")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("RAVIOLI_ENABLED", resp.json()["error"])

    # -- prune plan --
    def test_prune_plan_requires_superuser(self):
        User.objects.create_user("plain4", password="pw")
        self.client.login(username="plain4", password="pw")
        self.assertEqual(self.client.post("/ravioli/graph-prune/plan/").status_code, 302)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_prune_plan_disabled_returns_503(self):
        User.objects.create_superuser("admin7", email="a@b.c", password="pw")
        self.client.login(username="admin7", password="pw")
        resp = self.client.post("/ravioli/graph-prune/plan/", {"keep": 3})
        self.assertEqual(resp.status_code, 503)

    # -- shared apply (sync or prune) --
    def test_apply_requires_superuser(self):
        plan = self._ready_plan()
        User.objects.create_user("plain3", password="pw")
        self.client.login(username="plain3", password="pw")
        resp = self.client.post(f"/ravioli/graph/plan/{plan.id}/apply/")
        self.assertEqual(resp.status_code, 302)

    def test_apply_missing_plan_404(self):
        User.objects.create_superuser("admin6", email="a@b.c", password="pw")
        self.client.login(username="admin6", password="pw")
        resp = self.client.post("/ravioli/graph/plan/999999/apply/")
        self.assertEqual(resp.status_code, 404)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_apply_ready_plan_disabled_returns_503(self):
        plan = self._ready_plan(total=3)
        User.objects.create_superuser("admin5", email="a@b.c", password="pw")
        self.client.login(username="admin5", password="pw")
        resp = self.client.post(f"/ravioli/graph/plan/{plan.id}/apply/")
        self.assertEqual(resp.status_code, 503)


class StagedApplyTests(TestCase):
    class _Client:
        def __init__(self):
            self.calls = []

        def run_cypher(self, query, params=None):
            self.calls.append((query, params or {}))
            if "RETURN properties(n) AS props" in query:
                return [{"props": {"uuid": (params or {}).get("uuid"), "title": "OLD"}}]
            return []

        def close(self):
            pass

    def _plan_with_update(self):
        from toto.sql_neo4j_sync.models import GraphProjectionPlan
        diff = {
            "nodes": {
                "create": [], "delete": [], "ignored": [],
                "update": [{"label": "KanbanTask", "uuid": "t1", "props": {"title": "NEW"}}],
            },
            "relationships": {"create": [], "update": [], "delete": [], "ignored": []},
        }
        return GraphProjectionPlan.objects.create(
            status=GraphProjectionPlan.STATUS_READY, summary={"total_changes": 1}, diff=diff,
        )

    def test_staged_snapshots_updated_nodes_to_history(self):
        from toto.ravioli.services import graph_plans
        client = self._Client()
        graph_plans.apply_plan(client, self._plan_with_update(), staged=True)

        creates = [(q, p) for q, p in client.calls if q.startswith("CREATE (h:KanbanTask")]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0][1]["props"]["_prev_uuid"], "t1")
        self.assertTrue(creates[0][1]["props"]["_historical"])
        self.assertTrue(any("MERGE (n)-[:_HISTORICAL]->(h)" in q for q, _ in client.calls))

    def test_unstaged_overwrites_without_history(self):
        from toto.ravioli.services import graph_plans
        client = self._Client()
        graph_plans.apply_plan(client, self._plan_with_update(), staged=False)
        self.assertFalse(any(q.startswith("CREATE (h:") for q, _ in client.calls))


class PruneServiceTests(TestCase):
    class _PruneClient:
        """Returns two historical snapshots of one canonical KanbanTask node."""
        def run_cypher(self, query, params=None):
            if "h_labels" in query:
                return [
                    {"h_labels": ["KanbanTask"], "h_uuid": "t~1", "prev_uuid": "t"},
                    {"h_labels": ["KanbanTask"], "h_uuid": "t~2", "prev_uuid": "t"},
                ]
            return []

        def close(self):
            pass

    def test_create_prune_plan_builds_delete_diff(self):
        from toto.ravioli.services import graph_plans

        plan = graph_plans.create_prune_plan(self._PruneClient(), keep=1)
        self.assertEqual(plan.scope, {"kind": "prune", "keep": 1})

        node_del = plan.diff["nodes"]["delete"]
        self.assertEqual(node_del, [
            {"label": "KanbanTask", "uuid": "t~1"},
            {"label": "KanbanTask", "uuid": "t~2"},
        ])
        rel_del = plan.diff["relationships"]["delete"]
        self.assertEqual(len(rel_del), 2)
        self.assertEqual(rel_del[0]["relation"], "_HISTORICAL")
        self.assertEqual((rel_del[0]["from_uuid"], rel_del[0]["to_uuid"]), ("t", "t~1"))
        self.assertEqual(plan.total_changes, 4)  # 2 nodes + 2 edges

    @override_settings(RAVIOLI_DEFAULT_MAX_HISTORY=5)
    def test_default_keep_from_settings(self):
        from toto.ravioli.services.graph_plans import default_keep
        self.assertEqual(default_keep(), 5)


class HistoryViewTests(TestCase):
    def test_history_page_requires_superuser(self):
        User.objects.create_user("plain5", password="pw")
        self.client.login(username="plain5", password="pw")
        self.assertEqual(self.client.get("/ravioli/history/").status_code, 302)

    def test_history_data_requires_superuser(self):
        User.objects.create_user("plain6", password="pw")
        self.client.login(username="plain6", password="pw")
        self.assertEqual(self.client.get("/ravioli/history/data/").status_code, 302)

    @override_settings(RAVIOLI_ENABLED=False)
    def test_history_data_disabled_returns_503(self):
        User.objects.create_superuser("admin8", email="a@b.c", password="pw")
        self.client.login(username="admin8", password="pw")
        self.assertEqual(self.client.get("/ravioli/history/data/").status_code, 503)


class GraphHealthViewTests(TestCase):
    def test_requires_login(self):
        resp = self.client.get("/ravioli/graph-health/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url.lower())

    @override_settings(RAVIOLI_ENABLED=False)
    def test_disabled_reports_not_alive(self):
        User.objects.create_user("health", password="pw")
        self.client.login(username="health", password="pw")
        resp = self.client.get("/ravioli/graph-health/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data["enabled"])
        self.assertFalse(data["alive"])
