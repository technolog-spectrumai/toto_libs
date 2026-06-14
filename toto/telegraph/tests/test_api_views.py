import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from toto.telegraph.models import TelegraphChannel, TelegraphMember


class MeshGateApiViewTests(TestCase):
    """The decentralized data-mesh server gate: members read gated data from the server;
    non-members get 403 and must peer-pull."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="meshu", password="pass123")
        self.group, _ = Group.objects.get_or_create(name="data_mesh")

    def test_me_mesh_non_member(self):
        self.client.force_login(self.user)
        res = self.client.get("/telegraph/api/me/mesh/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data["member"])
        self.assertEqual(data["allowed"], [])
        self.assertIn("missions", data["gated"])

    def test_me_mesh_member(self):
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        data = self.client.get("/telegraph/api/me/mesh/").json()
        self.assertTrue(data["member"])
        self.assertIn("missions", data["allowed"])

    # Missions are the gated (mesh) domain; the gate short-circuits in dispatch before
    # the view runs, so a missing project id still 403s for a non-member.
    GATED_URL = "/kanban/api/projects/1/missions/"

    def test_gated_read_denied_for_non_member(self):
        self.client.force_login(self.user)
        res = self.client.get(self.GATED_URL)
        self.assertEqual(res.status_code, 403)
        self.assertTrue(res.json().get("gated"))

    def test_gated_read_allowed_for_member(self):
        self.user.groups.add(self.group)
        self.client.force_login(self.user)
        res = self.client.get(self.GATED_URL)
        # Member passes the gate (the view itself may 404 for a missing project).
        self.assertNotEqual(res.status_code, 403)
        self.assertNotEqual(res.status_code, 401)

    def test_non_member_blocked_unauthenticated(self):
        res = self.client.get(self.GATED_URL)
        self.assertEqual(res.status_code, 401)

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

    def test_me_includes_default_language(self):
        self.client.force_login(self.user)
        data = self.client.get("/telegraph/api/me/").json()
        self.assertEqual(data["language"], "en")

    def test_me_reflects_profile_language(self):
        from toto.people.models import Person
        Person.objects.create(user=self.user, display_name="Me", preferred_language="pl")
        self.client.force_login(self.user)
        data = self.client.get("/telegraph/api/me/").json()
        self.assertEqual(data["language"], "pl")

    def test_patch_language_unauthenticated(self):
        res = self.client.patch(
            "/telegraph/api/me/", data=json.dumps({"language": "pl"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 401)

    def test_patch_language_rejects_unsupported(self):
        self.client.force_login(self.user)
        res = self.client.patch(
            "/telegraph/api/me/", data=json.dumps({"language": "fr"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 400)

    def test_patch_language_without_profile_is_ok_but_not_stored(self):
        self.client.force_login(self.user)
        res = self.client.patch(
            "/telegraph/api/me/", data=json.dumps({"language": "pl"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["language"], "pl")
        self.assertFalse(body["stored"])

    def test_patch_language_persists_to_profile(self):
        from toto.people.models import Person
        person = Person.objects.create(user=self.user, display_name="Me", preferred_language="en")
        self.client.force_login(self.user)
        res = self.client.patch(
            "/telegraph/api/me/", data=json.dumps({"language": "pl"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["stored"])
        person.refresh_from_db()
        self.assertEqual(person.preferred_language, "pl")


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

    def test_members_include_username_for_aster_resolution(self):
        """Member dicts carry the SSO username (used to resolve a peer's gossip
        NodeId from aster), with an empty string for people who have no account."""
        from toto.people.models import Person

        linked = Person.objects.create(user=self.user, display_name="Det User")
        accountless = Person.objects.create(display_name="No Account")
        TelegraphMember.objects.create(channel=self.channel, person=linked, is_active=True)
        TelegraphMember.objects.create(channel=self.channel, person=accountless, is_active=True)

        data = self.client.get(f"/telegraph/api/channels/{self.channel.slug}/").json()
        by_name = {m["name"]: m for m in data["members"]}
        self.assertEqual(by_name["Det User"]["username"], "detuser")
        self.assertEqual(by_name["No Account"]["username"], "")


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


SMALL_WEBM = b"\x1a\x45\xdf\xa3"  # minimal EBML header (enough for content_type test)


class AudioUploadApiViewTests(TestCase):
    def setUp(self):
        from toto.people.models import Person
        self.user = User.objects.create_user(username="audiouser", password="pass")
        self.person = Person.objects.create(
            user=self.user, display_name="Audio User", email="audio@example.com"
        )
        self.channel = TelegraphChannel.objects.create(name="Audio", slug="audio")

    def test_upload_unauthenticated(self):
        f = SimpleUploadedFile("clip.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 401)

    def test_upload_invalid_type(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.exe", b"MZ", content_type="application/octet-stream")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 415)

    def test_upload_no_file(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/telegraph/api/channels/{self.channel.slug}/upload-audio/")
        self.assertEqual(res.status_code, 400)

    def test_upload_channel_not_found(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post("/telegraph/api/channels/nope/upload-audio/", {"audio": f})
        self.assertEqual(res.status_code, 404)

    @patch("toto.telegraph.api_views.get_channel_layer", return_value=None)
    def test_upload_success(self, _mock):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    @patch("toto.telegraph.api_views.get_channel_layer", return_value=None)
    def test_upload_ogg_accepted(self, _mock):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.ogg", b"OggS", content_type="audio/ogg")
        res = self.client.post(
            f"/telegraph/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 200)
