"""Persistent plaintext history: storage, replay shape, pagination and permanence.

This module replaced the encryption/TTL suite. Of the nine tests that lived here, eight
asserted properties that no longer exist (at-rest ciphertext, per-channel DEK isolation,
vault unavailability, secure-on-send opacity, TTL expiry and purge). Only
``test_history_oldest_first_with_metadata`` survived, ported to plaintext below.
"""
import shutil
import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from toto.forum import store
from toto.forum.models import ForumChannel, ForumMessage

User = get_user_model()

# Attachments are real files now, so the suite gets a throwaway attachment root rather
# than writing into the deployed one. They live outside MEDIA_ROOT on purpose — see
# models.forum_attachment_storage.
_MEDIA_ROOT = tempfile.mkdtemp(prefix="forum-test-media-")


@override_settings(FORUM_ATTACHMENT_ROOT=_MEDIA_ROOT, MEDIA_ROOT=_MEDIA_ROOT)
class HistoryTests(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.channel = ForumChannel.objects.create(name="hist", slug="hist")

    def test_message_is_stored_as_readable_plaintext(self):
        row = store.store_message(
            self.channel, msg_type="chat_message",
            body="launch is friday", sender_name="alice",
        )
        row.refresh_from_db()
        self.assertEqual(row.body, "launch is friday")
        # The whole point of the rework: the body is queryable.
        self.assertTrue(
            ForumMessage.objects.filter(body__icontains="friday").exists()
        )

    def test_history_oldest_first_with_metadata(self):
        store.store_message(self.channel, msg_type="chat_message", body="first", sender_name="a")
        store.store_message(self.channel, msg_type="chat_message", body="second", sender_name="b")
        hist = store.history(self.channel)
        self.assertEqual([h["message"] for h in hist], ["first", "second"])
        self.assertEqual(hist[0]["type"], "chat_message")
        self.assertEqual(hist[0]["user"], "a")
        self.assertTrue(hist[0]["id"])
        self.assertTrue(hist[0]["history"])

    def test_messages_are_permanent(self):
        """No TTL, no expiry filter, no purge — an old message still replays."""
        row = store.store_message(self.channel, msg_type="chat_message", body="ancient")
        ForumMessage.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=365 * 5)
        )
        hist = store.history(self.channel)
        self.assertEqual([h["message"] for h in hist], ["ancient"])

    def test_history_returns_newest_page_oldest_first(self):
        for i in range(10):
            store.store_message(self.channel, msg_type="chat_message", body=f"m{i}")
        hist = store.history(self.channel, limit=3)
        self.assertEqual([h["message"] for h in hist], ["m7", "m8", "m9"])

    def test_history_before_cursor_pages_backwards(self):
        for i in range(10):
            store.store_message(self.channel, msg_type="chat_message", body=f"m{i}")
        page1 = store.history(self.channel, limit=3)
        cursor = timezone.datetime.fromisoformat(page1[0]["created_at"])
        page2 = store.history(self.channel, limit=3, before=cursor,
                              before_id=page1[0]["id"])
        self.assertEqual([h["message"] for h in page2], ["m4", "m5", "m6"])

    def test_pagination_does_not_drop_messages_sharing_a_timestamp(self):
        """created_at alone is not a total order.

        With a timestamp-only cursor, two messages sharing a created_at that straddle a
        page boundary lose one of them permanently.
        """
        stamp = timezone.now()
        for i in range(6):
            row = store.store_message(self.channel, msg_type="chat_message", body=f"m{i}")
            ForumMessage.objects.filter(pk=row.pk).update(created_at=stamp)

        seen, cursor, cursor_id = [], None, None
        for _ in range(10):  # bounded; 6 messages at 2 per page needs 3
            page = store.history(self.channel, limit=2, before=cursor, before_id=cursor_id)
            if not page:
                break
            seen = [h["message"] for h in page] + seen
            cursor = timezone.datetime.fromisoformat(page[0]["created_at"])
            cursor_id = page[0]["id"]

        self.assertEqual(len(seen), 6, f"paging lost or repeated rows: {seen}")
        self.assertEqual(sorted(seen), [f"m{i}" for i in range(6)])

    def test_has_more_before(self):
        for i in range(5):
            store.store_message(self.channel, msg_type="chat_message", body=f"m{i}")
        hist = store.history(self.channel, limit=2)
        oldest = timezone.datetime.fromisoformat(hist[0]["created_at"])
        self.assertTrue(store.has_more_before(self.channel, oldest))
        full = store.history(self.channel, limit=50)
        first = timezone.datetime.fromisoformat(full[0]["created_at"])
        self.assertFalse(store.has_more_before(self.channel, first))

    def test_history_limit_is_clamped(self):
        for i in range(3):
            store.store_message(self.channel, msg_type="chat_message", body=f"m{i}")
        self.assertEqual(len(store.history(self.channel, limit=99999)), 3)

    def test_attachment_is_stored_on_disk_not_in_the_row(self):
        upload = SimpleUploadedFile("shot.png", b"\x89PNG-not-really", content_type="image/png")
        row = store.store_message(
            self.channel, msg_type="image_message", sender_name="alice",
            attachment=upload, attachment_name="shot.png",
            attachment_mime="image/png", attachment_size=15,
        )
        self.assertTrue(row.attachment.name.startswith(f"{self.channel.slug}/"))
        self.assertIn(str(row.id), row.attachment.name)
        payload = store.message_to_dict(row)
        self.assertIn("image_url", payload)
        self.assertNotIn("image_data", payload)  # no base64 inlining any more
        # The URL is the membership-checked view, never a raw /media/ path that nginx
        # would serve unauthenticated.
        self.assertIn(f"/forum/api/messages/{row.id}/attachment/", payload["image_url"])
        self.assertNotIn("/media/", payload["image_url"])

    def test_deleted_message_is_a_tombstone(self):
        row = store.store_message(self.channel, msg_type="chat_message", body="oops")
        row.deleted_at = timezone.now()
        row.save(update_fields=["deleted_at"])
        payload = store.message_to_dict(row)
        self.assertTrue(payload["deleted"])
        self.assertEqual(payload["message"], "")  # body is withheld, row survives

    def test_reply_and_edit_metadata_ride_the_payload(self):
        parent = store.store_message(self.channel, msg_type="chat_message", body="question")
        child = store.store_message(
            self.channel, msg_type="chat_message", body="answer", reply_to=parent
        )
        child.edited_at = timezone.now()
        child.save(update_fields=["edited_at"])
        payload = store.message_to_dict(child)
        self.assertEqual(payload["reply_to"], str(parent.id))
        self.assertIn("edited_at", payload)
