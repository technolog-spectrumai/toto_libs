from django.contrib.auth import get_user_model
from django.test import TestCase

from toto.people.models import Person
from toto.socialhub.models import Community
from toto.api.testutils import login_mesh_member

User = get_user_model()


class ProfileListApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        self.user1 = User.objects.create_user(username="alice", password="pass")
        self.user2 = User.objects.create_user(username="bob", password="pass")
        Person.objects.create(user=self.user1, display_name="Alice", email="alice@ex.com", slug="alice")
        Person.objects.create(user=self.user2, display_name="Bob", email="bob@ex.com", slug="bob")

    def test_list_returns_200(self):
        res = self.client.get("/socialhub/api/profiles/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("profiles", data)
        self.assertEqual(len(data["profiles"]), 2)

    def test_list_sorted_by_name(self):
        res = self.client.get("/socialhub/api/profiles/")
        names = [p["display_name"] for p in res.json()["profiles"]]
        self.assertEqual(names, sorted(names))


class ProfileDetailApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        self.user = User.objects.create_user(username="charlie", password="pass")
        self.person = Person.objects.create(
            user=self.user, display_name="Charlie", email="charlie@ex.com", slug="charlie"
        )

    def test_detail_found(self):
        res = self.client.get(f"/socialhub/api/profiles/{self.person.slug}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["slug"], "charlie")
        self.assertIn("communities", data)

    def test_detail_not_found(self):
        res = self.client.get("/socialhub/api/profiles/nonexistent-slug/")
        self.assertEqual(res.status_code, 404)


class CommunityListApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        Community.objects.create(name="Guild Alpha", slug="guild-alpha", org_type="guild")
        Community.objects.create(name="Zeta Corp", slug="zeta-corp", org_type="company")

    def test_list_returns_200(self):
        res = self.client.get("/socialhub/api/communities/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("communities", data)
        self.assertEqual(len(data["communities"]), 2)

    def test_list_sorted_by_name(self):
        res = self.client.get("/socialhub/api/communities/")
        names = [c["name"] for c in res.json()["communities"]]
        self.assertEqual(names, sorted(names))


class CommunityDetailApiTests(TestCase):
    def setUp(self):
        login_mesh_member(self)
        self.community = Community.objects.create(
            name="Test Community", slug="test-community", org_type="non_profit"
        )

    def test_detail_found(self):
        res = self.client.get(f"/socialhub/api/communities/{self.community.slug}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["name"], "Test Community")
        self.assertEqual(data["org_type"], "non_profit")
        self.assertIn("members", data)
        self.assertIn("member_count", data)

    def test_detail_not_found(self):
        res = self.client.get("/socialhub/api/communities/nope/")
        self.assertEqual(res.status_code, 404)
