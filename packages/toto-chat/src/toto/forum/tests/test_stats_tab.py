"""The room in numbers — counted right, member-gated."""

import tempfile
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from toto.forum.models import ForumChannel, ForumMember, ForumMessage

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class StatsBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        from toto.core.models import Platform
        from toto.people.models import Person

        Platform.objects.get_or_create(
            site_name="Toto", defaults={"author": "Test",
                                        "publication_year": 2026})
        cls.member_user = User.objects.create_user(username="m", password="x")
        cls.member_person = Person.objects.create(user=cls.member_user,
                                                  display_name="M")
        cls.outsider = User.objects.create_user(username="o", password="x")
        Person.objects.create(user=cls.outsider, display_name="O")
        cls.room = ForumChannel.objects.create(name="Alpha", slug="alpha")
        ForumMember.objects.create(channel=cls.room,
                                   person=cls.member_person, is_active=True)

    def _say(self, body="hi", channel=None, deleted=False):
        return ForumMessage.objects.create(
            channel=channel or self.room, sender=self.member_user,
            sender_name="M", body=body,
            deleted_at=timezone.now() if deleted else None)


class StatsTabTests(StatsBase):
    def test_a_non_member_is_refused(self):
        self.client.force_login(self.outsider)

        response = self.client.get(
            reverse("forum:room_stats", args=[self.room.slug]))

        self.assertEqual(response.status_code, 403)

    def test_counts_are_this_rooms_and_exclude_deleted(self):
        other = ForumChannel.objects.create(name="Beta", slug="beta")
        self._say()
        self._say()
        self._say(deleted=True)
        self._say(channel=other)
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_stats", args=[self.room.slug]))

        self.assertEqual(response.context["message_count"], 2)
        self.assertEqual(response.context["active_members"], 1)

    def test_the_day_series_has_one_row_per_day(self):
        """The Meta-ordering GROUP BY regression test: ForumMessage orders by
        created_at, and without the trailing order_by() the aggregate would
        return one row per MESSAGE."""
        import json

        self._say("one")
        self._say("two")
        self._say("three")
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_stats", args=[self.room.slug]))

        data = json.loads(response.context["day_chart_json"])
        self.assertEqual(len(data["labels"]), 30)
        self.assertEqual(sum(data["datasets"][0]["data"]), 3)
        today = data["datasets"][0]["data"][-1]
        self.assertEqual(today, 3)

    def test_the_hour_series_has_24_buckets(self):
        import json

        self._say()
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_stats", args=[self.room.slug]))

        data = json.loads(response.context["hour_chart_json"])
        self.assertEqual(len(data["labels"]), 24)
        self.assertEqual(sum(data["datasets"][0]["data"]), 1)

    def test_file_stats_count_the_room_library(self):
        from django.core.files.base import ContentFile

        from toto.forum import library
        from toto.vault.models import VaultFile

        User.objects.create_superuser(username="root")
        directory = library.ensure_channel_library(self.room)
        vault_file = VaultFile(owner=directory.bucket.owner,
                               bucket=directory.bucket, directory=directory,
                               title="doc.txt", file_type="txt")
        vault_file.file.save("doc.txt", ContentFile(b"x" * 2048), save=False)
        vault_file.save()
        # Two real megabytes of metadata, not of disk: the tile rounds to
        # one decimal, so anything under ~52 KB reads as 0.0.
        VaultFile.objects.filter(pk=vault_file.pk).update(
            file_size_bytes=2 * 1024 * 1024)
        self.client.force_login(self.member_user)

        response = self.client.get(
            reverse("forum:room_stats", args=[self.room.slug]))

        self.assertEqual(response.context["file_count"], 1)
        self.assertGreater(response.context["total_mb"], 0)
