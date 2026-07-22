import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from toto.bento import graph_service as gs
from toto.bento.models import BentoCategory, BentoEdgeType


class NodeApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("api", password="pw")
        BentoCategory.objects.create(name="Idea", slug="idea", neo4j_label="Idea")

    def test_list_nodes_returns_pagination_envelope(self):
        with patch.object(gs, "list_nodes", return_value=([{"uid": "u1"}], 1)) as mock:
            resp = self.client.get(reverse("bento:api_node_list"), {"page": 2, "per_page": 10, "category": "idea", "q": "x"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["page"], 2)
        # filter + search + offset forwarded
        _, kwargs = mock.call_args
        self.assertEqual(kwargs["cat_slug"], "idea")
        self.assertEqual(kwargs["q"], "x")
        self.assertEqual(kwargs["offset"], 10)

    def test_create_node_requires_auth(self):
        resp = self.client.post(reverse("bento:api_node_list"),
                                data=json.dumps({"category": "idea", "properties": {}}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_create_node_ok(self):
        self.client.login(username="api", password="pw")
        with patch.object(gs, "create_node", return_value={"uid": "u9"}) as mock:
            resp = self.client.post(reverse("bento:api_node_list"),
                                    data=json.dumps({"category": "idea", "properties": {"title": "T"}}),
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(json.loads(resp.content)["uid"], "u9")
        mock.assert_called_once_with("idea", {"title": "T"})

    def test_validation_error_maps_to_400(self):
        self.client.login(username="api", password="pw")
        with patch.object(gs, "create_node", side_effect=gs.GraphValidationError("bad")):
            resp = self.client.post(reverse("bento:api_node_list"),
                                    data=json.dumps({"category": "idea", "properties": {}}),
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_unavailable_maps_to_503(self):
        with patch.object(gs, "list_nodes", side_effect=gs.GraphUnavailable("off")):
            resp = self.client.get(reverse("bento:api_node_list"))
        self.assertEqual(resp.status_code, 503)


class EdgeApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("api", password="pw")

    def test_create_edge_requires_auth(self):
        resp = self.client.post(reverse("bento:api_edge_list"),
                                data=json.dumps({"edge_type": "supports", "from": "a", "to": "b"}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 401)

    def test_create_edge_ok(self):
        self.client.login(username="api", password="pw")
        with patch.object(gs, "create_edge", return_value={"id": "e1"}) as mock:
            resp = self.client.post(reverse("bento:api_edge_list"),
                                    data=json.dumps({"edge_type": "supports", "from": "a", "to": "b",
                                                     "properties": {"strength": 0.5}}),
                                    content_type="application/json")
        self.assertEqual(resp.status_code, 201)
        mock.assert_called_once_with("supports", "a", "b", {"strength": 0.5})

    def test_batch_delete_edges_via_html_view(self):
        from toto.core.models import Platform
        Platform.objects.create(site_name="T", author="T", publication_year=2024, active=True)
        self.client.login(username="api", password="pw")
        with patch.object(gs, "delete_edges", return_value=2) as mock:
            resp = self.client.post(reverse("bento:edge_batch_delete"), {"ids": ["e1", "e2"]})
        self.assertEqual(resp.status_code, 302)
        mock.assert_called_once()
        self.assertEqual(mock.call_args[0][0], ["e1", "e2"])


class TemplateApiTests(TestCase):
    def test_categories_and_edge_types_listed(self):
        cat = BentoCategory.objects.create(name="Idea", slug="idea", neo4j_label="Idea")
        et = BentoEdgeType.objects.create(name="Supports", slug="supports", rel_type="SUPPORTS")
        et.allowed_sources.set([cat])

        resp = self.client.get(reverse("bento:api_category_list"))
        self.assertEqual(json.loads(resp.content)["categories"][0]["slug"], "idea")

        resp = self.client.get(reverse("bento:api_edge_type_list"))
        ets = json.loads(resp.content)["edge_types"]
        self.assertEqual(ets[0]["rel_type"], "SUPPORTS")
        self.assertEqual(ets[0]["allowed_sources"], ["idea"])
