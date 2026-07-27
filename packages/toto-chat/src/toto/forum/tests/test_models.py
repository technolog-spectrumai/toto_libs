from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from toto.forum.models import ForumChannel, ForumMember

User = get_user_model()


class ForumChannelTests(TestCase):
    def test_create_channel(self):
        channel = ForumChannel.objects.create(name="General", slug="general")
        self.assertEqual(str(channel), "General")
        self.assertEqual(channel.slug, "general")

    def test_channel_ordering(self):
        ForumChannel.objects.create(name="Zebra", slug="zebra")
        ForumChannel.objects.create(name="Alpha", slug="alpha")
        names = list(ForumChannel.objects.values_list("name", flat=True))
        self.assertEqual(names, ["Alpha", "Zebra"])

    def test_membership_is_the_member_table_only(self):
        """There is no parallel ``participants`` M2M any more — see permissions.py."""
        channel = ForumChannel.objects.create(name="Room1", slug="room1")
        self.assertFalse(hasattr(channel, "participants"))


class ForumMemberTests(TestCase):
    def setUp(self):
        from toto.people.models import Person
        self.user = User.objects.create_user(username="bob", password="pass")
        self.person = Person.objects.create(
            user=self.user,
            display_name="Bob",
            email="bob@example.com",
        )
        self.channel = ForumChannel.objects.create(name="Lobby", slug="lobby")

    def test_create_member(self):
        member = ForumMember.objects.create(
            channel=self.channel,
            person=self.person,
            is_active=True,
        )
        self.assertEqual(member.display_name, self.person.full_name)
        self.assertEqual(member.participant_type, "human")
        self.assertTrue(str(member).endswith("in Lobby"))

    def test_member_clean_requires_person(self):
        member = ForumMember(channel=self.channel, person=None)
        with self.assertRaises(ValidationError):
            member.clean()

    def test_unique_channel_member_constraint(self):
        from django.db import IntegrityError
        ForumMember.objects.create(channel=self.channel, person=self.person)
        with self.assertRaises(IntegrityError):
            ForumMember.objects.create(channel=self.channel, person=self.person)

    def test_avatar_url_fallback(self):
        member = ForumMember(channel=self.channel, person=self.person)
        self.assertIn("default.png", member.avatar_url)
