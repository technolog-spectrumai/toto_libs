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


class ParticipantTypeRemovedTests(TestCase):
    """There is no human/non-human label on a member, and no room for one.

    ``participant_type`` returned the string ``"human"`` unconditionally, for
    every member, forever — the model's own CheckConstraint requires a person,
    so there was never a second value it could take. It was serialised into
    three payloads (the REST roster, the page context and the websocket
    ``room_participants`` frame) and read in exactly one place: a sidebar badge
    guarded on ``type == "ai_agent"``, a value nothing could produce.
    """

    def test_a_member_has_no_participant_type(self):
        self.assertFalse(hasattr(ForumMember, "participant_type"))

    def test_no_payload_still_ships_a_type(self):
        import inspect

        from toto.forum import api_views, consumers, views

        for module in (api_views, consumers, views):
            with self.subTest(module=module.__name__):
                self.assertNotIn("participant_type", inspect.getsource(module))

    def test_the_person_requirement_survives_the_label(self):
        """The constraint was never the label. A member still needs a person."""
        member = ForumMember(channel=None, person=None)
        with self.assertRaises(ValidationError):
            member.clean()


class ChannelCapTests(TestCase):
    """At most eight rooms, enforced where it actually runs.

    Rooms are cheap to make and expensive to keep — each carries a vault
    directory, a retention policy, a nightly cleanup pass and a websocket
    group — so the platform holds a small fixed number rather than a quota.
    """

    def test_the_cap_is_eight_by_default(self):
        from toto.forum.models import ForumChannel

        self.assertEqual(ForumChannel.max_channels(), 8)

    def test_the_ninth_room_is_refused(self):
        from django.core.exceptions import ValidationError

        from toto.forum.models import ForumChannel

        for i in range(8):
            ForumChannel.objects.create(name=f"Room {i}", slug=f"room-{i}")
        with self.assertRaises(ValidationError):
            ForumChannel.objects.create(name="Ninth", slug="ninth")
        self.assertEqual(ForumChannel.objects.count(), 8)

    def test_it_is_enforced_on_save_not_only_in_clean(self):
        """`clean()` runs for ModelForms and the admin; `objects.create()`
        never calls it. The admin and `ingress_forum.py` both make channels
        without a form — a cap only the create view honoured would be a cap
        in name. This is the same reasoning RESERVED_SLUGS records."""
        from django.core.exceptions import ValidationError

        from toto.forum.models import ForumChannel

        for i in range(8):
            ForumChannel.objects.create(name=f"R{i}", slug=f"r-{i}")
        # Straight to save(), bypassing full_clean() entirely.
        with self.assertRaises(ValidationError):
            ForumChannel(name="Bypass", slug="bypass").save()

    def test_an_existing_room_can_still_be_edited_at_capacity(self):
        """The cap is on CREATION. Renaming the eighth room must not be
        refused because the eighth room exists."""
        from toto.forum.models import ForumChannel

        rooms = [ForumChannel.objects.create(name=f"E{i}", slug=f"e-{i}")
                 for i in range(8)]
        rooms[-1].name = "Renamed"
        rooms[-1].save()          # must not raise
        self.assertEqual(ForumChannel.objects.get(pk=rooms[-1].pk).name,
                         "Renamed")

    def test_a_host_may_raise_the_cap(self):
        from django.test import override_settings

        from toto.forum.models import ForumChannel

        with override_settings(FORUM_MAX_CHANNELS=2):
            self.assertEqual(ForumChannel.max_channels(), 2)
