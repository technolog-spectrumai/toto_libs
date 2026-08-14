"""The room library: the vault scoped to one channel, whitelist-isolated."""

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
    def test_a_non_member_is_refused(self):
        self.client.force_login(self.outsider)

        response = self.client.get(
            reverse("forum:room_files", args=[self.room.slug]))

        self.assertEqual(response.status_code, 403)

    def test_the_tree_shows_only_this_rooms_files(self):
        other = ForumChannel.objects.create(name="Beta", slug="beta")
        self._file(self.room, "ours.txt")
        self._file(other, "theirs.txt")
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_files", args=[self.room.slug]))

        self.assertContains(response, "ours.txt")
        self.assertNotContains(response, "theirs.txt")

    def test_the_upload_button_points_at_the_rooms_gateway(self):
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_files", args=[self.room.slug]))

        self.room.refresh_from_db()
        self.assertContains(response, reverse(
            "vault:gateway_page", args=[self.room.vault_directory_id]))
