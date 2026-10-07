"""Images: checked by their bytes, kept sealed by the vault in the channel's
bucket, read through one door (stage 68, 2026-10-07).

    manage.py test toto.forum.tests.test_images
"""

from django.core.files.base import ContentFile

from toto.forum import channels, images
from toto.forum.models import ForumChannel, ForumMessage
from toto.forum.testing import GIF, JPEG, PNG, WEBP, ForumCase, client_of, send_json, upload
from toto.vault.models import Bucket, VaultFile
from toto.vault.storage_backends import persist_upload


class BucketTests(ForumCase):
    def test_the_channel_has_one_bucket_named_after_the_community(self):
        bucket = self.channel.bucket
        self.assertEqual((bucket.name, bucket.slug), ("Guild — forum", "forum-guild"))
        self.assertIsNone(bucket.owner_id)
        self.assertTrue(bucket.is_local)
        self.assertEqual(channels.ensure_bucket(self.channel).pk, bucket.pk)
        self.assertEqual(Bucket.objects.filter(slug__startswith="forum-").count(), 1)

    def test_a_taken_name_gets_a_suffix(self):
        Bucket.objects.create(name="Other — forum", slug="taken-elsewhere")
        Bucket.objects.create(name="Something", slug="forum-other")
        channel = channels.ensure_channel(self.other)
        self.assertEqual((channel.bucket.name, channel.bucket.slug),
                         ("Other — forum (2)", "forum-other-2"))

    def test_a_bucket_the_vault_lost_is_made_again(self):
        ForumChannel.objects.filter(pk=self.channel.pk).update(bucket=None)
        self.channel.refresh_from_db()
        Bucket.objects.filter(slug="forum-guild").delete()
        self.assertEqual(self.say(self.member, "pic", image=upload()).status_code, 201)
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.bucket.slug, "forum-guild")


class StoreTests(ForumCase):
    def test_an_image_is_stored_in_the_channels_bucket_through_the_vault(self):
        message = self.say(self.member, "look", image=upload()).json()["message"]
        self.assertEqual(message["kind"], "image")
        self.assertEqual(message["image"], {
            "url": self.url("message_image", message["id"]), "mime": "image/png",
            "size": len(PNG)})
        row = ForumMessage.objects.get(pk=message["id"])
        stored = row.attachment
        self.assertEqual(stored.bucket_id, self.channel.bucket_id)
        self.assertEqual(stored.owner, self.member)
        self.assertEqual(stored.key, f"forum-{row.id}")
        self.assertEqual(stored.notes, images.NOTE)
        self.assertTrue(stored.is_encrypted)
        self.assertFalse(stored.is_public)
        self.assertEqual((row.attachment_mime, row.attachment_size), ("image/png", len(PNG)))

    def test_an_image_alone_is_a_message(self):
        response = self.say(self.member, "", image=upload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["message"]["text"], "")

    def test_the_four_types_by_their_bytes(self):
        for data, mime in ((PNG, "image/png"), (JPEG, "image/jpeg"), (GIF, "image/gif"),
                           (WEBP, "image/webp")):
            # The name and the declared type lie; the bytes decide.
            message = self.say(self.member, "", image=upload(
                data, "notes.txt", "text/plain")).json()["message"]
            self.assertEqual(message["image"]["mime"], mime)
            served = client_of(self.second).get(self.url("message_image", message["id"]))
            self.assertEqual((served["Content-Type"], served.content), (mime, data))

    def test_what_is_not_an_image_is_refused_whatever_it_is_called(self):
        refused = [
            (b"<html><script>alert(1)</script></html>", "page.png", "image/png"),
            (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "a.svg",
             "image/svg+xml"),
            (b"PK\x03\x04" + b"\x00" * 30 + b"word/document.xml", "report.png", "image/png"),
            (b"%PDF-1.7 ...", "scan.jpg", "image/jpeg"),
            (b"just text", "t.gif", "image/gif"),
        ]
        for data, name, declared in refused:
            with self.subTest(name=name):
                response = self.say(self.member, "x", image=upload(data, name, declared))
                self.assertEqual(response.status_code, 415)
        self.assertEqual(self.say(self.member, "x", image=upload(b"", "e.png")).status_code, 400)
        self.assertEqual(ForumMessage.objects.count(), 0)
        self.assertEqual(VaultFile.all_objects.count(), 0)

    def test_too_large_is_refused(self):
        from unittest import mock

        with mock.patch.object(images, "MAX_BYTES", 16):
            response = self.say(self.member, "x", image=upload())
        self.assertEqual(response.status_code, 413)
        self.assertEqual(ForumMessage.objects.count(), 0)


class ReadTests(ForumCase):
    def setUp(self):
        super().setUp()
        self.message = self.say(self.member, "look", image=upload()).json()["message"]
        self.image_url = self.url("message_image", self.message["id"])

    def test_a_member_reads_it_with_its_real_type_and_no_sniffing(self):
        for user in (self.second, self.senior, self.head, self.admin):
            response = client_of(user).get(self.image_url)
            self.assertEqual(response.status_code, 200, user.username)
            self.assertEqual(response.content, PNG)
            self.assertEqual(response["Content-Type"], "image/png")
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response["Content-Disposition"], "inline")
            self.assertIn("no-store", response["Cache-Control"])

    def test_who_is_no_member_is_refused(self):
        self.assertEqual(client_of(self.outsider).get(self.image_url).status_code, 403)
        self.assertEqual(client_of(self.staff).get(self.image_url).status_code, 403)
        self.assertEqual(client_of(self.free).get(self.image_url).status_code, 402)

    def test_the_vaults_own_rule_opens_it_to_no_other_member(self):
        from toto.vault.access import may_read

        stored = ForumMessage.objects.get(pk=self.message["id"]).attachment
        for user in (self.second, self.head, self.outsider, self.staff):
            self.assertFalse(may_read(user, stored), user.username)

    def test_an_unrelated_file_in_the_same_bucket_is_not_the_forums(self):
        """Somebody's own file in the channel's bucket: no forum door reads
        it, and removing every message leaves it where it is."""
        unrelated = VaultFile(owner=self.second, title="minutes.txt", key="minutes",
                              file_type="text", bucket=self.channel.bucket)
        persist_upload(unrelated, ContentFile(b"the minutes", name="minutes.txt"))
        # No door names a vault file: an image is asked for by its message.
        self.assertEqual(client_of(self.second).get(
            self.url("message_image", "00000000-0000-0000-0000-000000000000")).status_code, 404)
        # A message that points at a file the forum did not seal serves nothing.
        row = ForumMessage.objects.get(pk=self.message["id"])
        own = row.attachment_id
        ForumMessage.objects.filter(pk=row.pk).update(attachment=unrelated)
        self.assertEqual(client_of(self.second).get(self.image_url).status_code, 409)
        ForumMessage.objects.filter(pk=row.pk).update(attachment_id=own)
        # Removing the message takes the forum's file and only that one.
        send_json(client_of(self.member), self.url("message_remove", self.message["id"]))
        left = VaultFile.all_objects.filter(bucket=self.channel.bucket)
        self.assertEqual([f.pk for f in left], [unrelated.pk])
        self.assertEqual(unrelated.file.read(), b"the minutes")

    def test_an_image_moved_under_another_message_does_not_open(self):
        other = self.say(self.member, "other", image=upload(JPEG, "o.jpg")).json()["message"]
        mine = ForumMessage.objects.get(pk=self.message["id"])
        ForumMessage.objects.filter(pk=other["id"]).update(attachment=mine.attachment,
                                                           attachment_mime="image/png")
        self.assertEqual(client_of(self.second).get(
            self.url("message_image", other["id"])).status_code, 409)

    def test_a_file_the_owner_deleted_in_the_vault_is_a_missing_image(self):
        ForumMessage.objects.get(pk=self.message["id"]).attachment.delete()
        self.assertEqual(client_of(self.second).get(self.image_url).status_code, 404)
        self.assertIsNone(self.feed(self.second).json()["messages"][0]["image"])

    def test_a_text_message_has_no_image(self):
        text = self.say(self.member, "words").json()["message"]
        self.assertEqual(client_of(self.second).get(
            self.url("message_image", text["id"])).status_code, 404)
