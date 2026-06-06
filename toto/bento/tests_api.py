import json

from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.bento.models import Category, IdeaBox

User = get_user_model()


class BoxListApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bentouser", password="pass")
        self.cat = Category.objects.create(name="Science", slug="science")
        IdeaBox.objects.create(label="Alpha", properties={"body": "first idea"}, category=self.cat)
        IdeaBox.objects.create(label="Beta", properties={"body": "second idea"})

    def test_list_returns_200(self):
        res = self.client.get("/bento/api/boxes/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("boxes", data)
        self.assertEqual(len(data["boxes"]), 2)

    def test_search_filters_results(self):
        res = self.client.get("/bento/api/boxes/?q=Alpha")
        data = res.json()
        self.assertEqual(len(data["boxes"]), 1)
        self.assertEqual(data["boxes"][0]["label"], "Alpha")

    def test_search_no_match(self):
        res = self.client.get("/bento/api/boxes/?q=zzznomatch")
        data = res.json()
        self.assertEqual(len(data["boxes"]), 0)

    def test_create_unauthenticated(self):
        res = self.client.post(
            "/bento/api/boxes/",
            json.dumps({"label": "New", "properties": {"body": "text"}}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_create_authenticated(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/bento/api/boxes/",
            json.dumps({"label": "New box", "properties": {"body": "content", "is_concept": False}}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertEqual(data["label"], "New box")
        self.assertFalse(data["is_concept"])
        self.assertEqual(data["properties"]["body"], "content")

    def test_create_with_category(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/bento/api/boxes/",
            json.dumps({"label": "Categorised", "category_id": self.cat.id}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["category_name"], "Science")

    def test_create_invalid_category(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/bento/api/boxes/",
            json.dumps({"label": "X", "category_id": 9999}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 404)


class BoxDetailApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="bentodet", password="pass")
        self.box = IdeaBox.objects.create(label="Detail box", properties={"body": "some content"})

    def test_get_box(self):
        res = self.client.get(f"/bento/api/boxes/{self.box.pk}/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["label"], "Detail box")

    def test_get_missing_box(self):
        res = self.client.get("/bento/api/boxes/99999/")
        self.assertEqual(res.status_code, 404)

    def test_patch_unauthenticated(self):
        res = self.client.patch(
            f"/bento/api/boxes/{self.box.pk}/",
            json.dumps({"label": "Updated"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_patch_updates_label(self):
        self.client.force_login(self.user)
        res = self.client.patch(
            f"/bento/api/boxes/{self.box.pk}/",
            json.dumps({"label": "Updated label"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["label"], "Updated label")
        self.box.refresh_from_db()
        self.assertEqual(self.box.label, "Updated label")

    def test_patch_updates_properties(self):
        self.client.force_login(self.user)
        res = self.client.patch(
            f"/bento/api/boxes/{self.box.pk}/",
            json.dumps({"properties": {"body": "new body", "rating": 5}}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.box.refresh_from_db()
        self.assertEqual(self.box.properties, {"body": "new body", "rating": 5})

    def test_delete_unauthenticated(self):
        res = self.client.delete(f"/bento/api/boxes/{self.box.pk}/")
        self.assertEqual(res.status_code, 401)

    def test_delete_authenticated(self):
        self.client.force_login(self.user)
        res = self.client.delete(f"/bento/api/boxes/{self.box.pk}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(IdeaBox.objects.filter(pk=self.box.pk).exists())

    def test_delete_missing(self):
        self.client.force_login(self.user)
        res = self.client.delete("/bento/api/boxes/99999/")
        self.assertEqual(res.status_code, 404)


class LinkApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="linkuser", password="pass")
        self.box_a = IdeaBox.objects.create(label="Alpha", properties={"body": "first"})
        self.box_b = IdeaBox.objects.create(label="Beta", properties={"body": "second"})

    def test_create_link_unauthenticated(self):
        res = self.client.post(
            "/bento/api/links/",
            json.dumps({"from_box": self.box_a.id, "to_box": self.box_b.id, "label": "relates"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_create_link_authenticated(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/bento/api/links/",
            json.dumps({"from_box": self.box_a.id, "to_box": self.box_b.id, "label": "supports"}),
            content_type="application/json",
        )
        self.assertIn(res.status_code, (200, 201))
        data = res.json()
        self.assertEqual(data["from_box"], self.box_a.id)
        self.assertEqual(data["to_box"], self.box_b.id)

    def test_create_link_self_reference(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/bento/api/links/",
            json.dumps({"from_box": self.box_a.id, "to_box": self.box_a.id, "label": ""}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_list_links_for_box(self):
        from toto.bento.models import IdeaLink
        IdeaLink.objects.create(from_box=self.box_a, to_box=self.box_b, label="answered by")
        res = self.client.get(f"/bento/api/boxes/{self.box_a.id}/links/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["links"]), 1)

    def test_delete_link_unauthenticated(self):
        from toto.bento.models import IdeaLink
        link = IdeaLink.objects.create(from_box=self.box_a, to_box=self.box_b, label="x")
        res = self.client.delete(f"/bento/api/links/{link.id}/")
        self.assertEqual(res.status_code, 401)

    def test_delete_link_authenticated(self):
        from toto.bento.models import IdeaLink
        self.client.force_login(self.user)
        link = IdeaLink.objects.create(from_box=self.box_a, to_box=self.box_b, label="x")
        res = self.client.delete(f"/bento/api/links/{link.id}/")
        self.assertEqual(res.status_code, 204)
        self.assertFalse(IdeaLink.objects.filter(pk=link.pk).exists())


class FullGraphApiTests(TestCase):
    def setUp(self):
        from toto.bento.models import IdeaLink
        self.cat = Category.objects.create(name="Science", slug="science")
        self.box_a = IdeaBox.objects.create(label="A", properties={"body": "aaa"}, category=self.cat)
        self.box_b = IdeaBox.objects.create(label="B", properties={"body": "bbb", "is_concept": True})
        IdeaLink.objects.create(from_box=self.box_a, to_box=self.box_b, label="leads to")

    def test_full_graph_returns_all(self):
        res = self.client.get("/bento/api/graph/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        # 2 box nodes + 1 category node
        self.assertEqual(len(data["nodes"]), 3)
        node_types = {n["node_type"] for n in data["nodes"]}
        self.assertIn("box", node_types)
        self.assertIn("concept", node_types)
        self.assertIn("category", node_types)

    def test_graph_edges_include_category_and_link(self):
        res = self.client.get("/bento/api/graph/")
        edges = res.json()["edges"]
        edge_types = {e["edge_type"] for e in edges}
        # IdeaLink edge
        self.assertIn("link", edge_types)
        # Category membership edge (box_a → cat)
        self.assertIn("category", edge_types)

    def test_concept_node_type(self):
        res = self.client.get("/bento/api/graph/")
        concept_nodes = [n for n in res.json()["nodes"] if n["node_type"] == "concept"]
        self.assertEqual(len(concept_nodes), 1)
        self.assertEqual(concept_nodes[0]["label"], "B")


class CategoryListApiTests(TestCase):
    def setUp(self):
        Category.objects.create(name="Art", slug="art")
        Category.objects.create(name="Zen", slug="zen")

    def test_list_returns_200(self):
        res = self.client.get("/bento/api/categories/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("categories", data)
        self.assertEqual(len(data["categories"]), 2)

    def test_list_sorted_by_name(self):
        res = self.client.get("/bento/api/categories/")
        names = [c["name"] for c in res.json()["categories"]]
        self.assertEqual(names, sorted(names))
