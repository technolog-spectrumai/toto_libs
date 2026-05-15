# studio/enigma/tests/test_enigma_py_api.py

from django.test import SimpleTestCase

from enigma_py import EnigmaEngine


class EnigmaPyApiTests(SimpleTestCase):
    def test_enigma_engine_two_party_roundtrip(self):
        room_slug = "test-room"
        alice_identity = "alice"
        bob_identity = "bob"

        alice_state = bytes(
            EnigmaEngine.create_group_state(
                room_slug,
                alice_identity,
            )
        )

        bob_state = bytes(
            EnigmaEngine.create_empty_state(
                room_slug,
                bob_identity,
            )
        )

        bob_state, bob_key_package = EnigmaEngine.key_package_from_state(
            bytes(bob_state)
        )

        alice_state, welcome, commit = EnigmaEngine.add_member_from_state(
            bytes(alice_state),
            bytes(bob_key_package),
        )

        self.assertTrue(bytes(welcome))
        self.assertTrue(bytes(commit))

        bob_state = EnigmaEngine.join_from_welcome_from_state(
            bytes(bob_state),
            bytes(welcome),
        )

        alice_state, alice_ciphertext = EnigmaEngine.encrypt_app_from_state(
            bytes(alice_state),
            b"hello bob",
        )

        bob_state, bob_plaintext = EnigmaEngine.process_message_from_state(
            bytes(bob_state),
            bytes(alice_ciphertext),
        )

        self.assertIsNotNone(bob_plaintext)
        self.assertEqual(bytes(bob_plaintext), b"hello bob")

        bob_state, bob_ciphertext = EnigmaEngine.encrypt_app_from_state(
            bytes(bob_state),
            b"hello alice",
        )

        alice_state, alice_plaintext = EnigmaEngine.process_message_from_state(
            bytes(alice_state),
            bytes(bob_ciphertext),
        )

        self.assertIsNotNone(alice_plaintext)
        self.assertEqual(bytes(alice_plaintext), b"hello alice")

        self.assertGreater(len(bytes(alice_state)), 0)
        self.assertGreater(len(bytes(bob_state)), 0)

    def test_enigma_engine_cannot_process_own_message(self):
        alice_state = bytes(
            EnigmaEngine.create_group_state(
                "self-message-room",
                "alice",
            )
        )

        alice_state, ciphertext = EnigmaEngine.encrypt_app_from_state(
            bytes(alice_state),
            b"hello myself",
        )

        with self.assertRaisesRegex(RuntimeError, "Cannot decrypt own messages"):
            EnigmaEngine.process_message_from_state(
                bytes(alice_state),
                bytes(ciphertext),
            )

    def test_enigma_engine_new_member_does_not_process_add_commit(self):
        alice_state = bytes(
            EnigmaEngine.create_group_state(
                "commit-test-room",
                "alice",
            )
        )

        bob_state = bytes(
            EnigmaEngine.create_empty_state(
                "commit-test-room",
                "bob",
            )
        )

        bob_state, bob_key_package = EnigmaEngine.key_package_from_state(
            bytes(bob_state)
        )

        alice_state, welcome, commit = EnigmaEngine.add_member_from_state(
            bytes(alice_state),
            bytes(bob_key_package),
        )

        bob_state = EnigmaEngine.join_from_welcome_from_state(
            bytes(bob_state),
            bytes(welcome),
        )

        with self.assertRaisesRegex(RuntimeError, "Message epoch differs"):
            EnigmaEngine.process_message_from_state(
                bytes(bob_state),
                bytes(commit),
            )