import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from toto.telegraph.models import TelegraphChannel, TelegraphMember

User = get_user_model()

SMALL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


class HealthApiViewTests(TestCase):
    def test_health_returns_telegraph(self):
        res = self.client.get("/telegraph/api/health/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["service"], "telegraph")


class LoginLogoutApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="pass123")

    def test_login_returns_token(self):
        res = self.client.post(
            "/telegraph/api/login/",
            json.dumps({"username": "testuser", "password": "pass123"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertIn("token", data)

    def test_login_wrong_password(self):
        res = self.client.post(
            "/telegraph/api/login/",
            json.dumps({"username": "testuser", "password": "wrong"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_logout(self):
        self.client.force_login(self.user)
        res = self.client.post("/telegraph/api/logout/")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])


class MeApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="meuser", password="pass", first_name="Me", last_name="User"
        )

    def test_me_unauthenticated(self):
        res = self.client.get("/telegraph/api/me/")
        self.assertEqual(res.status_code, 401)

    def test_me_authenticated(self):
        self.client.force_login(self.user)
        res = self.client.get("/telegraph/api/me/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["username"], "meuser")


class ChannelListApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listuser", password="pass")
        TelegraphChannel.objects.create(name="Alpha", slug="alpha")
        TelegraphChannel.objects.create(name="Beta", slug="beta")

    def test_channel_list_unauthenticated_allowed(self):
        res = self.client.get("/telegraph/api/channels/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("channels", data)
        self.assertEqual(len(data["channels"]), 2)

    def test_channels_sorted_by_name(self):
        res = self.client.get("/telegraph/api/channels/")
        names = [c["name"] for c in res.json()["channels"]]
        self.assertEqual(names, sorted(names))


class ChannelDetailApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="detuser", password="pass")
        self.channel = TelegraphChannel.objects.create(name="Detail", slug="detail")

    def test_channel_not_found(self):
        res = self.client.get("/telegraph/api/channels/nonexistent/")
        self.assertEqual(res.status_code, 404)

    def test_channel_found(self):
        res = self.client.get(f"/telegraph/api/channels/{self.channel.slug}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["slug"], "detail")
        self.assertIn("members", data)
        self.assertFalse(data["is_member"])


class ChannelJoinLeaveApiTests(TestCase):
    def setUp(self):
        from toto.people.models import Person
        self.user = User.objects.create_user(username="joinuser", password="pass")
        self.person = Person.objects.create(
            user=self.user, display_name="Join User", email="join@example.com"
        )
        self.channel = TelegraphChannel.objects.create(name="Joinable", slug="joinable")

    def test_join_unauthenticated(self):
        res = self.client.post(f"/telegraph/api/channels/{self.channel.slug}/join/")
        self.assertEqual(res.status_code, 401)

    def test_join_creates_member(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/telegraph/api/channels/{self.channel.slug}/join/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(TelegraphMember.objects.filter(channel=self.channel, person=self.person).exists())

    def test_leave_removes_member(self):
        self.client.force_login(self.user)
        TelegraphMember.objects.create(channel=self.channel, person=self.person, is_active=True)
        self.channel.participants.add(self.user)
        res = self.client.post(f"/telegraph/api/channels/{self.channel.slug}/leave/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            TelegraphMember.objects.filter(channel=self.channel, person=self.person, is_active=True).exists()
        )

    def test_leave_all(self):
        self.client.force_login(self.user)
        ch2 = TelegraphChannel.objects.create(name="Other", slug="other")
        for ch in (self.channel, ch2):
            TelegraphMember.objects.create(channel=ch, person=self.person, is_active=True)
            ch.participants.add(self.user)
        res = self.client.post("/telegraph/api/channels/leave-all/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["left"], 2)
        self.assertFalse(TelegraphMember.objects.filter(person=self.person, is_active=True).exists())


class ImageUploadApiViewTests(TestCase):
    def setUp(self):
        from toto.people.models import Person
        self.user = User.objects.create_user(username="imguser", password="pass")
        self.person = Person.objects.create(
            user=self.user, display_name="Img User", email="img@example.com"
        )
        self.channel = TelegraphChannel.objects.create(name="Images", slug="images")

    def test_upload_unauthenticated(self):
        f = SimpleUploadedFile("photo.png", SMALL_PNG, content_type="image/png")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 401)

    def test_upload_invalid_type(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("doc.pdf", b"%PDF-1.4", content_type="application/pdf")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 415)

    def test_upload_no_file(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/telegraph/api/channels/{self.channel.slug}/upload/")
        self.assertEqual(res.status_code, 400)

    @patch("toto.telegraph.api_views.get_channel_layer", return_value=None)
    def test_upload_success(self, _mock):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("photo.png", SMALL_PNG, content_type="image/png")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])

    def test_upload_channel_not_found(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("photo.png", SMALL_PNG, content_type="image/png")
        res = self.client.post("/telegraph/api/channels/nope/upload/", {"image": f})
        self.assertEqual(res.status_code, 404)
