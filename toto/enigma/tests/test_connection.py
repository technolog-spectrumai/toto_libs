from django.test import TestCase
from enigma.agent.connection import EnigmaConnection
from toto.enigma.models import Participant, ProcessedMessage, Room, State


class EnigmaConnectionTests(TestCase):
    def setUp(self):
        self.room = Room.objects.create(
            name="Test Room",
            slug="test-room",
        )

        self.alice_agent = self.make_agent(
            name="Alice Agent",
            slug="alice-agent",
        )

        self.bob_agent = self.make_agent(
            name="Bob Agent",
            slug="bob-agent",
        )

    def make_agent(self, *, name: str, slug: str):
        """
        Adjust this import/creation if your AgentProfile requires more fields.
        """
        from toto.steven.models import AgentProfile

        return AgentProfile.objects.create(
            name=name,
            slug=slug,
        )

    def test_creator_adds_joiner_and_sends_message(self):
        creator = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.alice_agent,
        )

        joiner = EnigmaConnection.create_joining_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        key_package = joiner.create_key_package()

        add_result = creator.add_member(
            key_package=bytes(key_package.public_key_package),
        )

        joiner.join_from_welcome(
            welcome=add_result.welcome,
            key_package=key_package,
        )

        ciphertext = creator.encrypt(b"hello bob")

        received = joiner.receive(
            message_id="msg-1",
            ciphertext=ciphertext,
        )

        self.assertIsNotNone(received)
        self.assertEqual(received.plaintext, b"hello bob")

    def test_joiner_can_reply_to_creator(self):
        creator = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.alice_agent,
        )

        joiner = EnigmaConnection.create_joining_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        key_package = joiner.create_key_package()

        add_result = creator.add_member(
            key_package=bytes(key_package.public_key_package),
        )

        joiner.join_from_welcome(
            welcome=add_result.welcome,
            key_package=key_package,
        )

        bob_ciphertext = joiner.encrypt(b"hello alice")

        received = creator.receive(
            message_id="msg-2",
            ciphertext=bob_ciphertext,
        )

        self.assertIsNotNone(received)
        self.assertEqual(received.plaintext, b"hello alice")

    def test_receive_is_idempotent_by_message_id(self):
        creator = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.alice_agent,
        )

        joiner = EnigmaConnection.create_joining_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        key_package = joiner.create_key_package()

        add_result = creator.add_member(
            key_package=bytes(key_package.public_key_package),
        )

        joiner.join_from_welcome(
            welcome=add_result.welcome,
            key_package=key_package,
        )

        ciphertext = creator.encrypt(b"hello once")

        first = joiner.receive(
            message_id="same-msg",
            ciphertext=ciphertext,
        )

        second = joiner.receive(
            message_id="same-msg",
            ciphertext=ciphertext,
        )

        self.assertIsNotNone(first)
        self.assertEqual(first.plaintext, b"hello once")
        self.assertIsNone(second)

        self.assertEqual(
            ProcessedMessage.objects.filter(
                state_id=joiner.state_id,
                message_id="same-msg",
            ).count(),
            1,
        )

    def test_key_package_marks_state_pending_and_used_after_welcome(self):
        creator = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.alice_agent,
        )

        joiner = EnigmaConnection.create_joining_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        key_package = joiner.create_key_package()

        joiner_state = State.objects.get(id=joiner.state_id)
        self.assertEqual(joiner_state.status, State.Status.PENDING_WELCOME)

        add_result = creator.add_member(
            key_package=bytes(key_package.public_key_package),
        )

        joiner.join_from_welcome(
            welcome=add_result.welcome,
            key_package=key_package,
        )

        key_package.refresh_from_db()
        joiner_state.refresh_from_db()

        self.assertIsNotNone(key_package.used_at)
        self.assertEqual(joiner_state.status, State.Status.ACTIVE)

    def test_state_rows_are_created_for_agent_participants(self):
        creator = EnigmaConnection.create_group_for_agent(
            room=self.room,
            agent=self.alice_agent,
        )

        joiner = EnigmaConnection.create_joining_for_agent(
            room=self.room,
            agent=self.bob_agent,
        )

        self.assertTrue(State.objects.filter(id=creator.state_id).exists())
        self.assertTrue(State.objects.filter(id=joiner.state_id).exists())

        self.assertEqual(
            Participant.objects.filter(room=self.room, agent=self.alice_agent).count(),
            1,
        )
        self.assertEqual(
            Participant.objects.filter(room=self.room, agent=self.bob_agent).count(),
            1,
        )

    def test_human_participant_cannot_have_connection(self):
        participant = Participant.objects.create(
            room=self.room,
            person=self.make_person(),
            is_active=True,
        )

        with self.assertRaises(ValueError):
            EnigmaConnection.for_participant(participant)

    def make_person(self):
        from toto.socialhub.models import Person

        return Person.objects.create(
            display_name="Human User",
            email="human@example.com",
        )