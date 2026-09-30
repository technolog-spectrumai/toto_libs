"""Two file stories that share a room, and only one of them is a tab.

`LibraryPlumbingTests` cover the vault library: a bucket directory per channel
whose whitelist is synced from membership. **It is no longer what the Files tab
shows**, but every one of those tests still matters — the files uploaded to it
are still there, still reachable through Storage, and still scoped by that
whitelist. If the sync stopped, somebody who left a room would keep vault
access to its old files forever, which is why parking the tab did not park the
plumbing.

`FilesTabTests` cover what the tab shows now: attachments posted in the room's
chat. The forum bar applies — a non-member is refused, room B's files never
appear in room A, and access dies when the member leaves.
"""

import tempfile

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse

from toto.forum import library
from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class LibraryBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.people.models import Person

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test",
                                        "publication_year": 2026})
        cls.root = User.objects.create_superuser(username="root")
        cls.member_user = User.objects.create_user(username="m", password="x")
        cls.member_person = Person.objects.create(user=cls.member_user,
                                                  display_name="M")
        cls.outsider = User.objects.create_user(username="o", password="x")
        Person.objects.create(user=cls.outsider, display_name="O")
        cls.room = ForumChannel.objects.create(name="Alpha", slug="alpha")
        cls.membership = ForumMember.objects.create(
            channel=cls.room, person=cls.member_person, is_active=True)

    def _file(self, channel, title="notes.txt"):
        from toto.vault.models import VaultFile

        directory = library.ensure_channel_library(channel)
        vault_file = VaultFile(owner=self.root, bucket=directory.bucket,
                               directory=directory, title=title,
                               file_type="txt")
        vault_file.file.save(title, ContentFile(b"hello"), save=False)
        vault_file.save()
        return vault_file


class LibraryPlumbingTests(LibraryBase):
    def test_ensure_is_idempotent(self):
        first = library.ensure_channel_library(self.room)
        second = library.ensure_channel_library(self.room)

        self.assertEqual(first.pk, second.pk)
        self.room.refresh_from_db()
        self.assertEqual(self.room.vault_directory_id, first.pk)

    def test_a_fresh_directory_never_has_an_empty_whitelist(self):
        """Vault reads an empty allowed_users as "every authenticated user" —
        the single biggest isolation hazard in this design."""
        empty_room = ForumChannel.objects.create(name="Empty", slug="empty")

        directory = library.ensure_channel_library(empty_room)

        self.assertGreater(directory.allowed_users.count(), 0)

    def test_a_forum_bucket_that_lost_its_owner_never_opens_a_room(self):
        """Bucket.owner is SET_NULL (2026-09-30). A new room's folder is then
        owned by the first superuser, and its whitelist is never None and
        never empty — empty would mean everybody."""
        from toto.vault.models import Bucket

        Bucket.objects.create(slug=library.FORUM_BUCKET_SLUG, name="Forum", owner=None)
        empty_room = ForumChannel.objects.create(name="Lonely", slug="lonely")

        directory = library.ensure_channel_library(empty_room)

        self.assertEqual(directory.owner, self.root)
        self.assertEqual(list(directory.allowed_users.all()), [self.root])

    def test_membership_drives_the_whitelist(self):
        directory = library.ensure_channel_library(self.room)
        self.assertIn(self.member_user,
                      directory.allowed_users.all())

        # Leaving revokes — through the same signal that kicks live sockets.
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])

        self.assertNotIn(self.member_user, directory.allowed_users.all())

    def test_the_gateway_whitelist_follows_too(self):
        directory = library.ensure_channel_library(self.room)
        gateway = directory.gateway

        self.assertIn(self.member_user, gateway.allowed_users.all())

    def test_a_member_may_download_and_an_outsider_may_not(self):
        vault_file = self._file(self.room)
        url = reverse("vault:public_file",
                      args=[vault_file.bucket.slug, vault_file.key])

        self.client.force_login(self.member_user)
        self.assertEqual(self.client.get(url).status_code, 200)

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)


class FilesTabTests(LibraryBase):
    """The tab lists what was posted in the chat, and offers no way in."""

    def _post(self, channel=None, *, name="photo.png", body="",
              msg_type="image_message", sender=None, size=1234):
        from toto.forum import store

        return store.store_message(
            channel or self.room, msg_type=msg_type, body=body,
            sender=sender or self.member_user, sender_name="M",
            attachment=ContentFile(b"bytes", name=name),
            attachment_name=name, attachment_mime="image/png",
            attachment_size=size)

    def _get(self, channel=None, **params):
        return self.client.get(
            reverse("forum:room_files", args=[(channel or self.room).slug]),
            params)

    def test_a_non_member_is_refused(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self._get().status_code, 403)

    def test_anonymous_is_sent_to_log_in(self):
        response = self._get()
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_it_lists_an_image_with_its_sender_and_date(self):
        self._post(name="holiday.png")
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertContains(response, "holiday.png")
        self.assertContains(response, "M")

    def test_a_voice_recording_is_listed_and_playable(self):
        self._post(name="voice-message.webm", msg_type="voice_message")
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertContains(response, "voice-message.webm")
        self.assertContains(response, "<audio")

    def test_room_bs_files_never_appear_in_room_a(self):
        other = ForumChannel.objects.create(name="Theirs", slug="theirs")
        self._post(channel=other, name="theirs.png")
        self._post(name="ours.png")
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertContains(response, "ours.png")
        self.assertNotContains(response, "theirs.png")

    def test_access_dies_when_the_member_leaves(self):
        self._post()
        self.membership.is_active = False
        self.membership.save(update_fields=["is_active"])
        self.client.force_login(self.member_user)
        self.assertEqual(self._get().status_code, 403)

    def test_a_deleted_message_takes_its_file_off_the_tab(self):
        """The file follows its message: withdrawing what you posted
        withdraws the file it carried, in one act."""
        from django.utils import timezone

        message = self._post(name="regret.png")
        self.client.force_login(self.member_user)
        self.assertContains(self._get(), "regret.png")
        message.deleted_at = timezone.now()
        message.save(update_fields=["deleted_at"])
        self.assertNotContains(self._get(), "regret.png")

    def test_a_message_with_no_attachment_is_not_a_file(self):
        from toto.forum import store

        store.store_message(self.room, msg_type="chat_message",
                            body="just talking", sender=self.member_user,
                            sender_name="M")
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertEqual(response.context["file_count"], 0)

    def test_there_is_no_way_to_upload_from_this_page(self):
        """The whole point of the rework: a file enters a room by being
        posted in its chat, and this page offers no second door."""
        self._post()
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertNotContains(response, "Share a file")
        self.assertNotContains(response, "gateway")
        self.assertNotContains(response, "<input type=\"file\"")

    def test_the_download_link_is_the_membership_checked_door(self):
        message = self._post()
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertContains(
            response, reverse("forum:api_message_attachment", args=[message.id]))
        self.assertNotContains(response, "/media/")

    def test_each_row_links_back_into_the_conversation(self):
        message = self._post()
        self.client.force_login(self.member_user)
        self.assertContains(self._get(), f"#msg-{message.id}")

    def test_the_kind_filter_narrows_the_list(self):
        self._post(name="pic.png")
        self._post(name="note.webm", msg_type="voice_message")
        self.client.force_login(self.member_user)
        self.assertContains(self._get(kind="image"), "pic.png")
        self.assertNotContains(self._get(kind="image"), "note.webm")
        self.assertContains(self._get(kind="voice"), "note.webm")

    def test_search_looks_at_the_words_posted_with_the_file(self):
        """A voice note is called voice-message.webm every single time, so
        names alone would make every recording unfindable."""
        self._post(name="voice-message.webm", msg_type="voice_message",
                   body="the budget discussion")
        self.client.force_login(self.member_user)
        self.assertContains(self._get(q="budget"), "voice-message.webm")
        self.assertNotContains(self._get(q="zzzz"), "voice-message.webm")

    def test_the_empty_states_say_different_things(self):
        self.client.force_login(self.member_user)
        self.assertContains(self._get(), "Nothing has been shared here yet.")
        self._post(name="pic.png")
        self.assertContains(self._get(q="zzzz"), "Nothing matches that.")

    def test_the_count_and_size_are_of_this_room_only(self):
        other = ForumChannel.objects.create(name="Theirs", slug="theirs2")
        self._post(channel=other, size=9999)
        self._post(size=1000)
        self._post(size=500)
        self.client.force_login(self.member_user)
        response = self._get()
        self.assertEqual(response.context["file_count"], 2)
        self.assertEqual(response.context["total_bytes"], 1500)
