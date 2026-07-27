import json
import shutil
import tempfile
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()

# Attachments are written to disk now, so uploads land in a throwaway root rather than the
# deployed media directory.
_MEDIA_ROOT = tempfile.mkdtemp(prefix="forum-test-media-")


def tearDownModule():
    shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)


# A one-pixel PNG: enough for content-type and storage assertions.
SMALL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0c"
    b"IDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00"
    b"\x00IEND\xaeB`\x82"
)


class ChannelListApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="listuser", password="pass")
        ForumChannel.objects.create(name="Alpha", slug="alpha")
        ForumChannel.objects.create(name="Beta", slug="beta")

    def test_channel_list_requires_authentication(self):
        """Messages are permanent now, so channel names are not disclosed anonymously."""
        res = self.client.get("/forum/api/channels/")
        self.assertEqual(res.status_code, 401)

    def test_channel_list_for_signed_in_user(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/api/channels/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("channels", data)
        self.assertEqual(len(data["channels"]), 2)
        self.assertFalse(data["channels"][0]["is_member"])

    def test_channels_sorted_by_name(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/api/channels/")
        names = [c["name"] for c in res.json()["channels"]]
        self.assertEqual(names, sorted(names))

    def test_create_channel_makes_the_creator_a_member(self):
        from toto.people.models import Person

        Person.objects.create(user=self.user, display_name="List User")
        self.client.force_login(self.user)
        res = self.client.post(
            "/forum/api/channels/", data=json.dumps({"name": "Fresh Room"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 201)
        self.assertEqual(res.json()["slug"], "fresh-room")
        detail = self.client.get("/forum/api/channels/fresh-room/").json()
        self.assertTrue(detail["is_member"])

    def test_create_channel_rejects_duplicates(self):
        self.client.force_login(self.user)
        res = self.client.post(
            "/forum/api/channels/", data=json.dumps({"name": "Alpha"}),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 409)


class ChannelDetailApiViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="detuser", password="pass")
        self.channel = ForumChannel.objects.create(name="Detail", slug="detail")

    def test_channel_detail_requires_authentication(self):
        res = self.client.get(f"/forum/api/channels/{self.channel.slug}/")
        self.assertEqual(res.status_code, 401)

    def test_channel_not_found(self):
        self.client.force_login(self.user)
        res = self.client.get("/forum/api/channels/nonexistent/")
        self.assertEqual(res.status_code, 404)

    def test_channel_found(self):
        self.client.force_login(self.user)
        res = self.client.get(f"/forum/api/channels/{self.channel.slug}/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["slug"], "detail")
        self.assertIn("members", data)
        self.assertFalse(data["is_member"])

    def test_roster_is_withheld_from_non_members(self):
        """A signed-in non-member sees the channel exists but not who is in it."""
        from toto.people.models import Person

        other = User.objects.create_user(username="outsider", password="pass")
        person = Person.objects.create(user=self.user, display_name="Det User")
        ForumMember.objects.create(channel=self.channel, person=person, is_active=True)

        self.client.force_login(other)
        data = self.client.get(f"/forum/api/channels/{self.channel.slug}/").json()
        self.assertEqual(data["members"], [])
        self.assertFalse(data["is_member"])

    def test_members_include_username_for_aster_resolution(self):
        """Member dicts carry the SSO username (used to resolve a peer's gossip
        NodeId from aster), with an empty string for people who have no account."""
        from toto.people.models import Person

        linked = Person.objects.create(user=self.user, display_name="Det User")
        accountless = Person.objects.create(display_name="No Account")
        ForumMember.objects.create(channel=self.channel, person=linked, is_active=True)
        ForumMember.objects.create(channel=self.channel, person=accountless, is_active=True)

        self.client.force_login(self.user)
        data = self.client.get(f"/forum/api/channels/{self.channel.slug}/").json()
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
        self.channel = ForumChannel.objects.create(name="Joinable", slug="joinable")

    def test_join_unauthenticated(self):
        res = self.client.post(f"/forum/api/channels/{self.channel.slug}/join/")
        self.assertEqual(res.status_code, 401)

    def test_join_creates_member(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/forum/api/channels/{self.channel.slug}/join/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertTrue(ForumMember.objects.filter(channel=self.channel, person=self.person).exists())

    def test_leave_removes_member(self):
        self.client.force_login(self.user)
        ForumMember.objects.create(channel=self.channel, person=self.person, is_active=True)
        res = self.client.post(f"/forum/api/channels/{self.channel.slug}/leave/")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(
            ForumMember.objects.filter(channel=self.channel, person=self.person, is_active=True).exists()
        )

    def test_leave_all(self):
        self.client.force_login(self.user)
        ch2 = ForumChannel.objects.create(name="Other", slug="other")
        for ch in (self.channel, ch2):
            ForumMember.objects.create(channel=ch, person=self.person, is_active=True)
        res = self.client.post("/forum/api/channels/leave-all/")
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["left"], 2)
        self.assertFalse(ForumMember.objects.filter(person=self.person, is_active=True).exists())


@override_settings(FORUM_ATTACHMENT_ROOT=_MEDIA_ROOT, MEDIA_ROOT=_MEDIA_ROOT)
class ImageUploadApiViewTests(TestCase):
    def setUp(self):
        from toto.people.models import Person
        self.user = User.objects.create_user(username="imguser", password="pass")
        self.person = Person.objects.create(
            user=self.user, display_name="Img User", email="img@example.com"
        )
        self.channel = ForumChannel.objects.create(name="Images", slug="images")
        # Uploading is posting, so it now requires an active membership row.
        ForumMember.objects.create(
            channel=self.channel, person=self.person, is_active=True
        )

    def test_upload_by_non_member_is_forbidden(self):
        outsider = User.objects.create_user(username="outsider-image", password="pass")
        self.client.force_login(outsider)
        f = SimpleUploadedFile("x.png", SMALL_PNG, content_type="image/png")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 403)

    def test_upload_unauthenticated(self):
        f = SimpleUploadedFile("photo.png", SMALL_PNG, content_type="image/png")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 401)

    def test_upload_invalid_type(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("doc.pdf", b"%PDF-1.4", content_type="application/pdf")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 415)

    def test_upload_no_file(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/forum/api/channels/{self.channel.slug}/upload/")
        self.assertEqual(res.status_code, 400)

    @patch("toto.forum.api_views.get_channel_layer", return_value=None)
    def test_upload_success(self, _mock):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("photo.png", SMALL_PNG, content_type="image/png")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload/",
            {"image": f},
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data["ok"])

    def test_upload_channel_not_found(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("photo.png", SMALL_PNG, content_type="image/png")
        res = self.client.post("/forum/api/channels/nope/upload/", {"image": f})
        self.assertEqual(res.status_code, 404)


SMALL_WEBM = b"\x1a\x45\xdf\xa3"  # minimal EBML header (enough for content_type test)


@override_settings(FORUM_ATTACHMENT_ROOT=_MEDIA_ROOT, MEDIA_ROOT=_MEDIA_ROOT)
class AudioUploadApiViewTests(TestCase):
    def setUp(self):
        from toto.people.models import Person
        self.user = User.objects.create_user(username="audiouser", password="pass")
        self.person = Person.objects.create(
            user=self.user, display_name="Audio User", email="audio@example.com"
        )
        self.channel = ForumChannel.objects.create(name="Audio", slug="audio")
        # Uploading is posting, so it now requires an active membership row.
        ForumMember.objects.create(
            channel=self.channel, person=self.person, is_active=True
        )

    def test_upload_by_non_member_is_forbidden(self):
        outsider = User.objects.create_user(username="outsider-audio", password="pass")
        self.client.force_login(outsider)
        f = SimpleUploadedFile("x.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 403)

    def test_upload_unauthenticated(self):
        f = SimpleUploadedFile("clip.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 401)

    def test_upload_invalid_type(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.exe", b"MZ", content_type="application/octet-stream")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 415)

    def test_upload_no_file(self):
        self.client.force_login(self.user)
        res = self.client.post(f"/forum/api/channels/{self.channel.slug}/upload-audio/")
        self.assertEqual(res.status_code, 400)

    def test_upload_channel_not_found(self):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post("/forum/api/channels/nope/upload-audio/", {"audio": f})
        self.assertEqual(res.status_code, 404)

    @patch("toto.forum.api_views.get_channel_layer", return_value=None)
    def test_upload_success(self, _mock):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.webm", SMALL_WEBM, content_type="audio/webm")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.json()["ok"])

    @patch("toto.forum.api_views.get_channel_layer", return_value=None)
    def test_upload_ogg_accepted(self, _mock):
        self.client.force_login(self.user)
        f = SimpleUploadedFile("clip.ogg", b"OggS", content_type="audio/ogg")
        res = self.client.post(
            f"/forum/api/channels/{self.channel.slug}/upload-audio/",
            {"audio": f},
        )
        self.assertEqual(res.status_code, 200)


@override_settings(FORUM_ATTACHMENT_ROOT=_MEDIA_ROOT, MEDIA_ROOT=_MEDIA_ROOT)
class MessageAttachmentApiViewTests(TestCase):
    """Attachments follow the same membership rule as the messages they belong to.

    They deliberately do not live under MEDIA_ROOT: nginx serves /media/ unauthenticated
    with a 30-day cache, which would leave a private channel's images readable forever by
    anyone who ever saw the URL — including a member who has since left.
    """

    def setUp(self):
        from toto.people.models import Person
        from toto.forum import store

        self.user = User.objects.create_user(username="att-member", password="pass")
        self.person = Person.objects.create(user=self.user, display_name="Att Member")
        self.channel = ForumChannel.objects.create(name="Private", slug="private")
        self.member = ForumMember.objects.create(
            channel=self.channel, person=self.person, is_active=True
        )
        upload = SimpleUploadedFile("shot.png", SMALL_PNG, content_type="image/png")
        self.row = store.store_message(
            self.channel, msg_type="image_message", sender=self.user,
            sender_name="Att Member", attachment=upload, attachment_name="shot.png",
            attachment_mime="image/png", attachment_size=len(SMALL_PNG),
        )
        self.url = f"/forum/api/messages/{self.row.id}/attachment/"

    def test_member_can_fetch_the_attachment(self):
        self.client.force_login(self.user)
        res = self.client.get(self.url)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "image/png")
        self.assertEqual(b"".join(res.streaming_content), SMALL_PNG)

    def test_anonymous_is_rejected(self):
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_non_member_is_rejected(self):
        outsider = User.objects.create_user(username="att-outsider", password="pass")
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_access_is_revoked_when_the_member_leaves(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 200)
        self.member.is_active = False
        self.member.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_soft_deleted_message_hides_its_attachment(self):
        from django.utils import timezone

        self.row.deleted_at = timezone.now()
        self.row.save(update_fields=["deleted_at"])
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 404)
