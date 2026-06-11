"""Tests for the per-object "Export to graph" feature.

Covers three layers:
  * ProjectionRunner.export_node_one_hop  — node + neighbour + edge projection
  * loader.label_for_model                — model → graph-label registry
  * the view guards + the inclusion tag    — UI wiring
"""

from django.contrib.auth.models import Group, User
from django.test import SimpleTestCase, TestCase, override_settings

from toto.sql_neo4j_sync.projection import ProjectionRunner


class FakeNeo4jClient:
    def __init__(self):
        self.calls = []

    def run_cypher(self, query, params=None):
        self.calls.append((query, params or {}))
        return []

    def close(self):
        pass


# A self-contained graph config that reuses Django's auth models so the test
# needs no app-specific fixtures: User --MEMBER_OF--> Group (a real M2M).
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


class ExportNodeOneHopTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("alice")
        self.g1 = Group.objects.create(name="editors")
        self.g2 = Group.objects.create(name="reviewers")
        self.user.groups.add(self.g1, self.g2)

    def test_export_upserts_node_neighbours_and_edges(self):
        client = FakeNeo4jClient()
        runner = ProjectionRunner(client, AUTH_CONFIGS)

        summary = runner.export_node_one_hop("TUser", "alice")

        queries = [q for q, _ in client.calls]

        # The node itself.
        self.assertTrue(any("MERGE (n:TUser" in q for q in queries))
        # Both neighbour nodes were upserted so the edges have a target.
        self.assertEqual(sum("MERGE (n:TGroup" in q for q in queries), 2)
        # Stale edges cleared, then one edge merged per group.
        self.assertTrue(any("[r:MEMBER_OF]->() DELETE r" in q for q in queries))
        self.assertEqual(
            sum("MERGE (a)-[r:MEMBER_OF]->(b)" in q for q in queries), 2
        )

        self.assertEqual(summary["neighbours"], 2)
        self.assertEqual(summary["label"], "TUser")
        self.assertEqual(
            summary["relations"],
            [{"relation": "MEMBER_OF", "to_label": "TGroup", "count": 2}],
        )

    def test_export_with_no_neighbours_reports_zero(self):
        lonely = User.objects.create_user("bob")
        client = FakeNeo4jClient()
        runner = ProjectionRunner(client, AUTH_CONFIGS)

        summary = runner.export_node_one_hop("TUser", "bob")

        self.assertEqual(summary["neighbours"], 0)
        self.assertEqual(summary["relations"], [])
        # No TGroup upserts and no edge merges when the user has no groups.
        self.assertFalse(any("MERGE (n:TGroup" in q for q, _ in client.calls))


class LabelForModelTests(SimpleTestCase):
    def test_mapped_model_resolves_to_label(self):
        from toto.events.models import ScheduledEvent
        from toto.sql_neo4j_sync.loader import build_label_by_model

        # Build from real YAML, bypassing the process cache for determinism.
        mapping = build_label_by_model()
        self.assertEqual(mapping.get(ScheduledEvent), ("Event", "uid"))

    def test_unmapped_model_returns_none(self):
        from toto.sql_neo4j_sync.loader import build_label_by_model

        self.assertIsNone(build_label_by_model().get(User))


class ExportButtonTagTests(SimpleTestCase):
    def _render(self, obj):
        from toto.core.templatetags.graph_export import export_to_graph_button

        return export_to_graph_button({"csrf_token": "tok"}, obj)

    def test_hidden_when_object_is_none(self):
        self.assertEqual(self._render(None), {"show": False})

    @override_settings(RAVIOLI_ENABLED=False)
    def test_hidden_when_graph_disabled(self):
        self.assertEqual(self._render(User()), {"show": False})

    @override_settings(RAVIOLI_ENABLED=True)
    def test_hidden_for_unmapped_model(self):
        self.assertEqual(self._render(User()), {"show": False})

    @override_settings(RAVIOLI_ENABLED=True)
    def test_shown_for_mapped_model(self):
        from toto.events.models import ScheduledEvent

        ctx = self._render(ScheduledEvent())
        self.assertTrue(ctx["show"])
        self.assertEqual(ctx["graph_label"], "Event")
        self.assertEqual(ctx["app_label"], "events")
        self.assertEqual(ctx["model_name"], "scheduledevent")


class ExportViewGuardTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("carol", password="pw")

    def test_requires_login(self):
        resp = self.client.post(
            "/sql-neo4j-sync/export/events/scheduledevent/abc/"
        )
        # login_required bounces anonymous users to the login page.
        self.assertEqual(resp.status_code, 302)
        self.assertIn("login", resp.url.lower())

    @override_settings(RAVIOLI_ENABLED=False)
    def test_disabled_graph_redirects_with_message(self):
        self.client.login(username="carol", password="pw")
        resp = self.client.post(
            "/sql-neo4j-sync/export/events/scheduledevent/abc/",
            HTTP_REFERER="/events/",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/events/")

    @override_settings(RAVIOLI_ENABLED=True)
    def test_unmapped_model_redirects_with_message(self):
        self.client.login(username="carol", password="pw")
        resp = self.client.post(
            "/sql-neo4j-sync/export/auth/user/carol/",
            HTTP_REFERER="/somewhere/",
        )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/somewhere/")
